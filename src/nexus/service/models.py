import enum
from pathlib import Path
from pydantic import BaseModel
from typing import List


class JobStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ServiceStatus(enum.Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class CreateJobRequest(BaseModel):
    command: str
    env_vars: dict[str, str] | None = None


class Job(BaseModel):
    id: str
    command: str
    status: JobStatus
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    gpu_index: int | None = None
    screen_session: str | None = None
    env_vars: List[tuple[str, str]] = []
    exit_code: int | None = None
    error_message: str | None = None
    log_dir: Path | None = None

    class Config:
        arbitrary_types_allowed = True


class ServiceState(BaseModel):
    status: ServiceStatus = ServiceStatus.STOPPED
    jobs: List[Job] = []
    blacklisted_gpus: List[int] = []
    is_paused: bool = False
    last_updated: float = 0.0
