import datetime as dt
import json
import pathlib
import time

from nexus.service import models
from nexus.service.logger import logger


def create_default_state(state_path: pathlib.Path) -> models.ServiceState:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch(exist_ok=True)

    default_state = models.ServiceState(
        status="running",
        jobs=[],
        blacklisted_gpus=[],
        last_updated=0.0,
    )

    save_state(default_state, state_path)
    return default_state


def load_state(state_path: pathlib.Path) -> models.ServiceState:
    """Load service state from disk"""

    if not state_path.exists():
        logger.info("State file not found. Initializing default.")
        return create_default_state(state_path)

    try:
        data = json.loads(state_path.read_text())
        state = models.ServiceState.model_validate(data)
        logger.info("Successfully loaded state from disk.")
        return state
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to load state from disk. Initializing default.")
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
            logger.info(f"Backed up corrupted state file to {backup_path}")
        return create_default_state(state_path)


def save_state(state: models.ServiceState, state_path: pathlib.Path) -> None:
    """Save service state to disk"""
    temp_path = state_path.with_suffix(".json.tmp")

    state.last_updated = dt.datetime.now().timestamp()

    try:
        json_data = state.model_dump_json(indent=2)
        temp_path.write_text(json_data)
        temp_path.replace(state_path)

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def get_job_by_id(state: models.ServiceState, job_id: str) -> models.Job | None:
    """Get a job by its ID"""
    return next((job for job in state.jobs if job.id == job_id), None)


def remove_completed_jobs(state: models.ServiceState, history_limit: int) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed = [j for j in state.jobs if j.status in ("completed", "failed")]
    if len(completed) > history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[:history_limit]
        active_jobs = [j for j in state.jobs if j.status in ("queued", "running")]
        state.jobs = active_jobs + keep_jobs


def update_jobs_in_state(state: models.ServiceState, jobs: list[models.Job]) -> None:
    """Update multiple jobs in the state"""
    job_dict = {job.id: job for job in jobs}
    for i, existing_job in enumerate(state.jobs):
        if existing_job.id in job_dict:
            state.jobs[i] = job_dict[existing_job.id]
    state.last_updated = time.time()


def add_jobs_to_state(state: models.ServiceState, jobs: list[models.Job]) -> None:
    """Add new jobs to the state"""
    state.jobs.extend(jobs)
    state.last_updated = dt.datetime.now().timestamp()


def remove_jobs_from_state(state: models.ServiceState, job_ids: list[str]) -> bool:
    """Remove multiple jobs from the state"""
    original_length = len(state.jobs)
    state.jobs = [j for j in state.jobs if j.id not in job_ids]

    if len(state.jobs) != original_length:
        state.last_updated = dt.datetime.now().timestamp()
        return True

    return False


def clean_old_completed_jobs_in_state(state: models.ServiceState, max_completed: int) -> models.ServiceState:
    """Remove old completed jobs keeping only the most recent ones"""
    completed_jobs = [j for j in state.jobs if j.status in ["completed", "failed"]]

    if len(completed_jobs) > max_completed:
        # Sort by completion time
        completed_jobs.sort(key=lambda x: x.completed_at or 0, reverse=True)

        # Keep only the most recent ones
        jobs_to_keep = completed_jobs[:max_completed]
        job_ids_to_keep = {j.id for j in jobs_to_keep}

        # Filter jobs
        state.jobs = [j for j in state.jobs if j.status not in ["completed", "failed"] or j.id in job_ids_to_keep]

        state.last_updated = dt.datetime.now().timestamp()

    return state
