import typing

import pydantic as pyd


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


class GpuInfo(pyd.BaseModel):
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool
    running_job_id: str | None


class ServiceStatus(pyd.BaseModel):
    running: bool
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    is_paused: bool


class ServiceState(pyd.BaseModel):
    status: typing.Literal["running", "stopped", "error"]
    jobs: list[Job]
    blacklisted_gpus: list[int]
    is_paused: bool
    last_updated: float
