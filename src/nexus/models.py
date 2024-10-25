import dataclasses as dc
import enum
import pathlib


class JobStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dc.dataclass
class Job:
    id: str
    command: str
    status: JobStatus
    created_at: float
    started_at: float | None
    completed_at: float | None
    gpu_index: int | None
    screen_session: str | None
    env_vars: list[tuple[str, str]]
    exit_code: int | None
    error_message: str | None
    log_dir: pathlib.Path | None


@dc.dataclass
class Config:
    log_dir: pathlib.Path
    refresh_rate: int
    history_limit: int


@dc.dataclass
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool = False


@dc.dataclass
class NexusState:
    jobs: list[Job]
    blacklisted_gpus: list[int]
    is_paused: bool
    last_updated: float
