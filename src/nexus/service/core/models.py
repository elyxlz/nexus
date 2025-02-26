import dataclasses
import pathlib as pl
import typing

import pydantic as pyd

__all__ = [
    "JobStatus",
    "Job",
    "GpuInfo",
    "FrozenBaseModel",
    "JobsRequest",
    "ServiceLogsResponse",
    "ServiceActionResponse",
    "JobLogsResponse",
    "JobActionResponse",
    "JobQueueActionResponse",
    "GpuActionError",
    "GpuActionResponse",
    "ServiceStatusResponse",
]

JobStatus = typing.Literal["queued", "running", "completed", "failed"]


# we use these instead of frozen basemodels because the type checker recognizes immutability
@dataclasses.dataclass(frozen=True)
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


@dataclasses.dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    process_count: int
    is_blacklisted: bool
    running_job_id: str | None


# Response and Request models


class FrozenBaseModel(pyd.BaseModel):
    model_config = pyd.ConfigDict(frozen=True)


class JobsRequest(FrozenBaseModel):
    commands: list[str]
    git_repo_url: str
    git_tag: str
    user: str | None
    discord_id: str | None


class ServiceLogsResponse(FrozenBaseModel):
    logs: str


class ServiceActionResponse(FrozenBaseModel):
    status: str


class JobLogsResponse(FrozenBaseModel):
    logs: str


class JobActionResponse(FrozenBaseModel):
    killed: list[str]
    failed: list[dict]


class JobQueueActionResponse(FrozenBaseModel):
    removed: list[str]
    failed: list[dict]


class GpuActionError(FrozenBaseModel):
    index: int
    error: str


class GpuActionResponse(FrozenBaseModel):
    blacklisted: list[int] | None
    removed: list[int] | None
    failed: list[GpuActionError]


class ServiceStatusResponse(FrozenBaseModel):
    running: bool
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    service_user: str
    service_version: str
