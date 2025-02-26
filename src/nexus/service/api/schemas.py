import pydantic as pyd

__all__ = [
    "JobsRequest",
    "ServiceLogsResponse",
    "ServiceActionResponse",
    "JobLogsResponse",
    "JobActionError",
    "JobActionResponse",
    "JobQueueActionError",
    "JobQueueActionResponse",
    "GpuActionError",
    "GpuActionResponse",
    "ServiceStatusResponse",
]


class FrozenBaseModel(pyd.BaseModel):
    model_config = pyd.ConfigDict(frozen=True)


class JobsRequest(FrozenBaseModel):
    commands: list[str]
    git_repo_url: str
    git_tag: str
    user: str | None = None
    discord_id: str | None = None


class ServiceLogsResponse(FrozenBaseModel):
    logs: str


class ServiceActionResponse(FrozenBaseModel):
    status: str


class JobLogsResponse(FrozenBaseModel):
    logs: str


class JobActionError(FrozenBaseModel):
    id: str
    error: str


class JobActionResponse(FrozenBaseModel):
    killed: list[str]
    failed: list[JobActionError]


class JobQueueActionError(FrozenBaseModel):
    id: str
    error: str


class JobQueueActionResponse(FrozenBaseModel):
    removed: list[str]
    failed: list[JobQueueActionError]


class GpuActionError(FrozenBaseModel):
    index: int
    error: str


class GpuActionResponse(FrozenBaseModel):
    blacklisted: list[int] | None = None
    removed: list[int] | None = None
    failed: list[GpuActionError]


class ServiceStatusResponse(FrozenBaseModel):
    running: bool
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    service_user: str
    service_version: str
