import dataclasses as dc
import pathlib as pl
import typing as tp

__all__ = ["JobStatus", "NotificationType", "Job"]

JobStatus = tp.Literal["queued", "running", "completed", "failed"]
NotificationType = tp.Literal["discord"]


@dc.dataclass(frozen=True)
class Job:
    id: str
    command: str
    user: str
    git_repo_url: str
    git_tag: str
    git_branch: str
    status: JobStatus
    created_at: float
    node_name: str
    env: dict[str, str]
    jobrc: str | None
    search_wandb: bool
    notifications: list[NotificationType]
    notification_messages: dict[str, str]  # {discord_start:12352}

    pid: int | None
    dir: pl.Path | None
    started_at: float | None
    gpu_index: int | None
    wandb_url: str | None
    marked_for_kill: bool

    completed_at: float | None
    exit_code: int | None
    error_message: str | None
