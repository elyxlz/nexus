import dataclasses as dc
import pathlib as pl
import typing as tp

__all__ = ["JobStatus", "Job"]

JobStatus = tp.Literal["queued", "running", "completed", "failed"]


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
    env: dict[str, str]
    jobrc: str | None
    search_wandb: bool
    notifications: list[str]
    discord_start_notification_message_id: str | None  # need this for editing messages

    pid: int | None
    dir: pl.Path | None
    started_at: float | None
    gpu_index: int | None
    wandb_url: str | None
    marked_for_kill: bool

    completed_at: float | None
    exit_code: int | None
    error_message: str | None
