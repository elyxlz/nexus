import dataclasses as dc
import json
import pathlib as pl

from nexus.service import logger, models


def create_default_state() -> models.NexusServiceState:
    default_state = models.NexusServiceState(status="running", jobs=(), blacklisted_gpus=())
    return default_state


def load_state(state_path: pl.Path) -> models.NexusServiceState:
    """Load service state from disk"""
    data = json.loads(state_path.read_text())
    state = models.NexusServiceState(**data)
    logger.info("Successfully loaded state from disk.")
    return state


def save_state(state: models.NexusServiceState, state_path: pl.Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch(exist_ok=True)
    temp_path = state_path.with_suffix(".json.tmp")
    try:
        json_data = dc.asdict(state)
        serialized = json.dumps(json_data, default=str, indent=2)
        temp_path.write_text(serialized)
        temp_path.replace(state_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def get_job_by_id(state: models.NexusServiceState, job_id: str) -> models.Job | None:
    """Get a job by its ID"""
    return next((job for job in state.jobs if job.id == job_id), None)


def remove_completed_jobs(state: models.NexusServiceState, history_limit: int) -> models.NexusServiceState:
    """Remove old completed jobs keeping only the most recent ones"""
    completed = [j for j in state.jobs if j.status in ("completed", "failed")]
    if len(completed) > history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[:history_limit]
        active_jobs = [j for j in state.jobs if j.status in ("queued", "running")]
        state = dc.replace(state, jobs=active_jobs + keep_jobs)
    return state


def update_jobs_in_state(state: models.NexusServiceState, jobs: list[models.Job]) -> models.NexusServiceState:
    """Update multiple jobs in the state"""
    job_dict = {job.id: job for job in jobs}
    new_jobs = [job_dict.get(existing_job.id, existing_job) for existing_job in state.jobs]
    return dc.replace(state, jobs=new_jobs)


def add_jobs_to_state(state: models.NexusServiceState, jobs: list[models.Job]) -> models.NexusServiceState:
    """Add new jobs to the state"""
    new_jobs = list(state.jobs) + list(jobs)
    return dc.replace(state, jobs=new_jobs)


def remove_jobs_from_state(state: models.NexusServiceState, job_ids: list[str]) -> models.NexusServiceState:
    new_jobs = [j for j in state.jobs if j.id not in job_ids]
    return dc.replace(state, jobs=new_jobs)


def clean_old_completed_jobs_in_state(state: models.NexusServiceState, max_completed: int) -> models.NexusServiceState:
    """Remove old completed jobs keeping only the most recent ones"""
    completed_jobs = [j for j in state.jobs if j.status in ["completed", "failed"]]

    if len(completed_jobs) <= max_completed:
        return state

    # Sort by completion time
    completed_jobs.sort(key=lambda x: x.completed_at or 0, reverse=True)
    # Keep only the most recent ones
    jobs_to_keep = completed_jobs[:max_completed]
    job_ids_to_keep = {j.id for j in jobs_to_keep}

    # Create new filtered jobs list
    new_jobs = [j for j in state.jobs if j.status not in ["completed", "failed"] or j.id in job_ids_to_keep]

    return dc.replace(state, jobs=new_jobs)
