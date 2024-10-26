import datetime as dt
import json
import pathlib
import time

from nexus.service import models


def load_state(state_path: pathlib.Path) -> models.ServiceState:
    """Load service state from disk"""

    default_state = models.ServiceState(
        status="running",
        jobs=[],
        blacklisted_gpus=[],
        is_paused=False,
        last_updated=0.0,
    )

    if not state_path.exists():
        return default_state

    try:
        data = json.loads(state_path.read_text())
        state = models.ServiceState.model_validate(data)
        return state
    except (json.JSONDecodeError, ValueError):
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
        return default_state


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


def remove_completed_jobs(
    state: models.ServiceState, history_limit: int, state_path: pathlib.Path
) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed = [j for j in state.jobs if j.status in ("completed", "failed")]
    if len(completed) > history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[:history_limit]
        active_jobs = [j for j in state.jobs if j.status in ("queued", "running")]
        state.jobs = active_jobs + keep_jobs
        save_state(state, state_path)


def update_jobs_in_state(
    state: models.ServiceState, jobs: list[models.Job], state_path: pathlib.Path
) -> None:
    """Update multiple jobs in the state"""
    job_dict = {job.id: job for job in jobs}
    for i, existing_job in enumerate(state.jobs):
        if existing_job.id in job_dict:
            state.jobs[i] = job_dict[existing_job.id]
    state.last_updated = time.time()
    save_state(state, state_path)


def add_jobs_to_state(
    state: models.ServiceState, jobs: list[models.Job], state_path: pathlib.Path
) -> None:
    """Add new jobs to the state"""
    state.jobs.extend(jobs)
    state.last_updated = dt.datetime.now().timestamp()
    save_state(state, state_path)


def remove_jobs_from_state(
    state: models.ServiceState, job_ids: list[str], state_path: pathlib.Path
) -> bool:
    """Remove multiple jobs from the state"""
    original_length = len(state.jobs)
    state.jobs = [j for j in state.jobs if j.id not in job_ids]

    if len(state.jobs) != original_length:
        state.last_updated = dt.datetime.now().timestamp()
        save_state(state, state_path)
        return True

    return False


def clean_old_completed_jobs_in_state(
    state: models.ServiceState, max_completed: int, state_path: pathlib.Path
) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed_jobs = [j for j in state.jobs if j.status in ["completed", "failed"]]

    if len(completed_jobs) > max_completed:
        # Sort by completion time
        completed_jobs.sort(key=lambda x: x.completed_at or 0, reverse=True)

        # Keep only the most recent ones
        jobs_to_keep = completed_jobs[:max_completed]
        job_ids_to_keep = {j.id for j in jobs_to_keep}

        # Filter jobs
        state.jobs = [
            j
            for j in state.jobs
            if j.status not in ["completed", "failed"] or j.id in job_ids_to_keep
        ]

        state.last_updated = dt.datetime.now().timestamp()
        save_state(state, state_path)
