import datetime as dt
import typing as tp

from nexus.server.core import schemas

__all__ = ["format_runtime", "format_timestamp", "calculate_runtime", "format_job_action"]


def format_runtime(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def format_timestamp(timestamp: float | None) -> str:
    if not timestamp:
        return "Unknown"
    return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def calculate_runtime(job: schemas.Job) -> float:
    if not job.started_at:
        return 0.0
    if job.status in ["completed", "failed", "killed"] and job.completed_at:
        return job.completed_at - job.started_at
    elif job.status == "running":
        return dt.datetime.now().timestamp() - job.started_at
    return 0.0


def format_job_action(
    job: schemas.Job, action: tp.Literal["added", "started", "completed", "failed", "killed", "updated"]
) -> str:
    runtime = calculate_runtime(job)
    gpu_info = f" on GPU(s) {','.join(map(str, job.gpu_idxs))}" if job.gpu_idxs else ""
    time_info = ""

    if action == "added":
        time_info = f" at {format_timestamp(job.created_at)}"
    elif action == "started":
        time_info = f" at {format_timestamp(job.started_at)}"
    elif action in ("completed", "failed", "killed"):
        time_info = f" after {format_runtime(runtime)}"
    elif action == "updated":
        time_info = f" at {format_timestamp(job.created_at)}"

    error_info = f" ({job.error_message})" if job.error_message else ""
    git_info = f" [Git Tag: {job.git_tag}, Git URL: {job.git_repo_url}]" if job.git_tag and job.git_repo_url else ""

    return f"Job {job.id} {action}{gpu_info}{time_info}: COMMAND: {job.command}{error_info}{git_info}"
