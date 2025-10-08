import dataclasses as dc
import pathlib as pl
import typing as tp

__all__ = ["JobStatus", "NotificationType", "IntegrationType", "Job"]


def _exclude_env_repr(obj):
    return {k: v for k, v in dc.asdict(obj).items() if k != "env"}


JobStatus = tp.Literal["queued", "running", "completed", "failed", "killed"]
NotificationType = tp.Literal["discord", "phone"]
IntegrationType = tp.Literal["wandb", "nullpointer"]


@dc.dataclass(frozen=True, slots=True)
class Job:
    id: str
    command: str
    artifact_id: str
    git_repo_url: str | None
    git_branch: str | None
    git_tag: str | None
    status: JobStatus
    created_at: float
    priority: int
    num_gpus: int
    env: dict[str, str]
    node_name: str
    jobrc: str | None
    integrations: list[IntegrationType]
    notifications: list[NotificationType]
    notification_messages: dict[str, str]
    pid: int | None
    dir: pl.Path | None
    started_at: float | None
    gpu_idxs: list[int]
    wandb_url: str | None
    marked_for_kill: bool
    completed_at: float | None
    exit_code: int | None
    error_message: str | None
    user: str
    ignore_blacklist: bool
    screen_session_name: str | None

    def __repr__(self) -> str:
        return str(_exclude_env_repr(self))
