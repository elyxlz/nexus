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
    user: str
    artifact_id: str
    git_repo_url: str | None
    git_branch: str | None
    priority: int
    num_gpus: int
    node_name: str
    env: dict[str, str]
    jobrc: str | None
    notifications: list[NotificationType]
    integrations: list[IntegrationType]

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
