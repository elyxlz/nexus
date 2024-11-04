import datetime as dt
import typing

from nexus.service import models


def format_runtime(seconds: float) -> str:
    """Format runtime in seconds to h m s."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def format_timestamp(timestamp: float | None) -> str:
    """Format timestamp to human-readable string."""
    if not timestamp:
        return "Unknown"
    return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def calculate_runtime(job: models.Job) -> float:
    """Calculate runtime from job timestamps."""
    if not job.started_at:
        return 0.0
    if job.status == "completed" and job.completed_at:
        return job.completed_at - job.started_at
    elif job.status == "running":
        return dt.datetime.now().timestamp() - job.started_at
    return 0.0


def format_job_action(job: models.Job, action: typing.Literal["added", "started", "completed", "failed"]) -> str:
    """Format a job action log message with consistent structure."""
    runtime = calculate_runtime(job)
    gpu_info = f" on GPU {job.gpu_index}" if job.gpu_index is not None else ""
    time_info = ""

    if action == "added":
        time_info = f" at {format_timestamp(job.created_at)}"
    elif action == "started":
        time_info = f" at {format_timestamp(job.started_at)}"
    elif action in ("completed", "failed"):
        time_info = f" after {format_runtime(runtime)}"

    error_info = f" ({job.error_message})" if job.error_message else ""
    git_info = f" [Git Tag: {job.git_tag}, Git URL: {job.git_repo_url}]" if job.git_tag and job.git_repo_url else ""

    return f"Job {job.id} {action}{gpu_info}{time_info}: COMMAND: {job.command}{error_info}{git_info}"
