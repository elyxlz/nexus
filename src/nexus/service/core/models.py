import dataclasses as dc
import pathlib as pl
import typing

__all__ = ["JobStatus", "Job", "GpuInfo"]

JobStatus = typing.Literal["queued", "running", "completed", "failed"]


@dc.dataclass(frozen=True)
class Job:
    id: str
    command: str
    git_repo_url: str
    git_tag: str
    status: JobStatus
    created_at: float
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


@dc.dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    process_count: int
    is_blacklisted: bool
    running_job_id: str | None
