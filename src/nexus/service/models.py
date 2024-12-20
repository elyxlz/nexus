import typing

import pydantic as pyd

JobStatus = typing.Literal["queued", "running", "completed", "failed"]


class Job(pyd.BaseModel):
    id: str
    command: str
    git_repo_url: str
    git_tag: str
    status: JobStatus
    created_at: float
    started_at: float | None
    completed_at: float | None
    gpu_index: int | None
    exit_code: int | None
    error_message: str | None
    wandb_url: str | None
    user: str | None
    discord_id: str | None
    marked_for_kill: bool


class GpuInfo(pyd.BaseModel):
    index: int
    name: str
    memory_total: int
    memory_used: int
    process_count: int
    is_blacklisted: bool
    running_job_id: str | None
    is_available: bool


class ServiceState(pyd.BaseModel):
    status: typing.Literal["running", "stopped", "error"]
    jobs: list[Job]
    blacklisted_gpus: list[int]
    last_updated: float


# Response and Request models


class JobsRequest(pyd.BaseModel):
    commands: list[str]
    git_repo_url: str
    git_tag: str
    user: str | None
    discord_id: str | None


class ServiceLogsResponse(pyd.BaseModel):
    logs: str


class ServiceActionResponse(pyd.BaseModel):
    status: str


class JobLogsResponse(pyd.BaseModel):
    logs: str


class JobActionResponse(pyd.BaseModel):
    killed: list[str]
    failed: list[dict]


class JobQueueActionResponse(pyd.BaseModel):
    removed: list[str]
    failed: list[dict]


class GpuActionError(pyd.BaseModel):
    index: int
    error: str


class GpuActionResponse(pyd.BaseModel):
    blacklisted: list[int] | None
    removed: list[int] | None
    failed: list[GpuActionError]


class ServiceStatusResponse(pyd.BaseModel):
    running: bool
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    service_user: str
    service_version: str
