import json
import pathlib
import time

from nexus.service.models import Job, ServiceState


def load_state(state_path: pathlib.Path) -> ServiceState:
    """Load service state from disk"""

    if not state_path.exists():
        return ServiceState()

    try:
        data = json.loads(state_path.read_text())
        state = ServiceState.model_validate(data)
        return state
    except (json.JSONDecodeError, ValueError):
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
        return ServiceState()


def save_state(state: ServiceState, state_path: pathlib.Path) -> None:
    """Save service state to disk"""
    temp_path = state_path.with_suffix(".json.tmp")

    state.last_updated = time.time()

    try:
        # Use pydantic's json serialization
        json_data = state.model_dump_json(indent=2)
        temp_path.write_text(json_data)
        temp_path.replace(state_path)

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def get_job_by_id(state: ServiceState, job_id: str) -> Job | None:
    """Get a job by its ID"""
    return next((job for job in state.jobs if job.id == job_id), None)


def remove_completed_jobs(
    state: ServiceState, history_limit: int, state_path: pathlib.Path
) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed = [j for j in state.jobs if j.status in ("completed", "failed")]
    if len(completed) > history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[:history_limit]
        active_jobs = [j for j in state.jobs if j.status in ("queued", "running")]
        state.jobs = active_jobs + keep_jobs
        save_state(state, state_path)


def update_job(state: ServiceState, job: Job, state_path: pathlib.Path) -> None:
    """Update a job in the state"""
    for i, existing_job in enumerate(state.jobs):
        if existing_job.id == job.id:
            state.jobs[i] = job
            break
    state.last_updated = time.time()
    save_state(state, state_path)


def add_job(state: ServiceState, job: Job, state_path: pathlib.Path) -> None:
    """Add a new job to the state"""
    state.jobs.append(job)
    state.last_updated = time.time()
    save_state(state, state_path)


def remove_job(state: ServiceState, job_id: str, state_path: pathlib.Path) -> bool:
    """Remove a job from the state"""
    original_length = len(state.jobs)
    state.jobs = [j for j in state.jobs if j.id != job_id]

    if len(state.jobs) != original_length:
        state.last_updated = time.time()
        save_state(state, state_path)
        return True

    return False


def clean_completed_jobs(
    state: ServiceState, max_completed: int, state_path: pathlib.Path
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

        state.last_updated = time.time()
        save_state(state, state_path)
