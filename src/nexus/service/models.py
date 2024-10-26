import pathlib
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
    working_dir: pathlib.Path


class GpuInfo(pyd.BaseModel):
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool
    running_job_id: str | None


class ServiceState(pyd.BaseModel):
    status: typing.Literal["running", "stopped", "error"]
    jobs: list[Job]
    blacklisted_gpus: list[int]
    is_paused: bool
    last_updated: float


# Response and Request models


class JobsRequest(pyd.BaseModel):
    commands: list[str]
    working_dir: str  # single working directory for all commands in the batch


class ServiceLogsResponse(pyd.BaseModel):
    logs: str


class ServiceActionResponse(pyd.BaseModel):
    status: str


class JobLogsResponse(pyd.BaseModel):
    stdout: str
    stderr: str


class JobActionResponse(pyd.BaseModel):
    killed: list[str]
    failed: list[dict]


class JobQueueActionResponse(pyd.BaseModel):
    removed: list[str]
    failed: list[dict]


class GpuActionResponse(pyd.BaseModel):
    status: str


class ServiceStatusResponse(pyd.BaseModel):
    running: bool
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    is_paused: bool
