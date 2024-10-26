# nexus/service/state.py
import json
import pathlib
import time
from typing import Any, Dict, List
from .models import Job, JobStatus, ServiceState


def get_state_path() -> pathlib.Path:
    """Get the path to the state file"""
    return pathlib.Path.home() / ".nexus" / "state.json"


def serialize_job(job: Job) -> Dict[str, Any]:
    """Convert a job to a serializable dictionary"""
    job_dict = job.model_dump()
    job_dict["status"] = job_dict["status"].value
    return job_dict


def deserialize_job(job_dict: Dict[str, Any]) -> Job:
    """Convert a dictionary back to a Job"""
    job_dict["status"] = JobStatus(job_dict["status"])
    return Job(**job_dict)


def load_state(config_log_dir: Path) -> ServiceState:
    """Load service state from disk"""
    state_path = config_log_dir / "state.json"

    if not state_path.exists():
        return ServiceState()

    try:
        data = json.loads(state_path.read_text())
        state = ServiceState.model_validate(data)
        return state
    except (json.JSONDecodeError, ValueError) as e:
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
        return ServiceState()


def save_state(state: ServiceState, config_log_dir: Path) -> None:
    """Save service state to disk"""
    state_path = config_log_dir / "state.json"
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


def remove_completed_jobs(state: ServiceState, history_limit: int) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed = [j for j in state.jobs if j.status in ("completed", "failed")]
    if len(completed) > history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[:history_limit]
        active_jobs = [j for j in state.jobs if j.status in ("queued", "running")]
        state.jobs = active_jobs + keep_jobs


def update_job(state: ServiceState, job: Job) -> None:
    """Update a job in the state and persist changes"""
    for i, existing_job in enumerate(state.jobs):
        if existing_job.id == job.id:
            state.jobs[i] = job
            break
    state.last_updated = time.time()
    save_state(state)


def add_job(state: ServiceState, job: Job) -> None:
    """Add a new job to the state and persist changes"""
    state.jobs.append(job)
    state.last_updated = time.time()
    save_state(state)


def remove_job(state: ServiceState, job_id: str) -> bool:
    """Remove a job from the state and persist changes"""
    original_length = len(state.jobs)
    state.jobs = [j for j in state.jobs if j.id != job_id]

    if len(state.jobs) != original_length:
        state.last_updated = time.time()
        save_state(state)
        return True
    return False


def clean_completed_jobs(state: ServiceState, max_completed: int = 1000) -> None:
    """Remove old completed jobs keeping only the most recent ones"""
    completed_jobs = [
        j for j in state.jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]

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
            if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
            or j.id in job_ids_to_keep
        ]

        state.last_updated = time.time()
        save_state(state)
