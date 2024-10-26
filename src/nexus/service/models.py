import typing
import pydantic as pyd


class GpuInfo(pyd.BaseModel):
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool


class Job(pyd.BaseModel):
    id: str
    command: str
    status: typing.Literal["queued", "running", "completed", "failed"]
    created_at: float
    started_at: float | None
    completed_at: float | None
    gpu_index: int | None
    screen_session: str | None
    exit_code: int | None
    error_message: str | None


class ServiceState(pyd.BaseModel):
    status: typing.Literal["running", "stopped", "error"] = "running"
    jobs: list[Job] = []
    blacklisted_gpus: list[int] = []
    is_paused: bool = False
    last_updated: float = 0.0
