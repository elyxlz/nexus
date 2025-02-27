import dataclasses as dc
import pathlib as pl
import typing as tp

__all__ = ["JobStatus", "Job"]

JobStatus = tp.Literal["queued", "running", "completed", "failed"]


@dc.dataclass(frozen=True)
class Job:
    id: str
    command: str
    git_repo_url: str
    git_tag: str
    status: JobStatus
    created_at: float
    pid: int | None
    dir: pl.Path | None
    started_at: float | None
    completed_at: float | None
    gpu_index: int | None
    exit_code: int | None
    error_message: str | None
    wandb_url: str | None
    user: str | None
    discord_id: str | None
    marked_for_kill: bool
    webhook_message_id: str | None
