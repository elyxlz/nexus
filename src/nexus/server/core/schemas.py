import dataclasses as dc
import pathlib as pl
import typing as tp

__all__ = ["JobStatus", "NotificationType", "Job"]


def _exclude_env_repr(obj):
    return {k: v for k, v in dc.asdict(obj).items() if k != "env"}


JobStatus = tp.Literal["queued", "running", "completed", "failed", "killed"]
NotificationType = tp.Literal["discord", "phone"]


@dc.dataclass(frozen=True)
class Job:
    id: str
    command: str
    user: str
    git_repo_url: str
    git_tag: str
    git_branch: str
    priority: int
    num_gpus: int
    node_name: str
    env: dict[str, str]
    jobrc: str | None
    notifications: list[NotificationType]
    search_wandb: bool

    status: JobStatus
    created_at: float

    notification_messages: dict[str, str]
    pid: int | None
    dir: pl.Path | None
    started_at: float | None
    gpu_idxs: list[int]
    wandb_url: str | None
    marked_for_kill: bool
    ignore_blacklist: bool
    screen_session_name: str | None

    completed_at: float | None
    exit_code: int | None
    error_message: str | None

    def __repr__(self) -> str:
        return str(_exclude_env_repr(self))
