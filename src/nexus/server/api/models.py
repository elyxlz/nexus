import pydantic as pyd
import typing_extensions as tpe

from nexus.server.core import schemas

__all__ = [
    "JobRequest",
    "ServerLogsResponse",
    "ServerActionResponse",
    "JobLogsResponse",
    "JobActionError",
    "JobActionResponse",
    "JobQueueActionError",
    "JobQueueActionResponse",
    "GpuActionError",
    "GpuActionResponse",
    "ServerStatusResponse",
    "HealthResponse",
    "HeartbeatResponse",
]

REQUIRED_ENV_VARS = {
    "wandb": ["WANDB_API_KEY", "WANDB_ENTITY"],
    "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"],
    "phone": ["PHONE_TO_NUMBER"],
}


class FrozenBaseModel(pyd.BaseModel):
    model_config = pyd.ConfigDict(frozen=True)


class JobRequest(FrozenBaseModel):
    command: str
    user: str
    git_repo_url: str
    git_tag: str
    git_branch: str
    num_gpus: int = 1
    gpu_idxs: list[int] = []
    priority: int = 0
    search_wandb: bool = False
    notifications: list[schemas.NotificationType] = []
    env: dict[str, str] = {}
    jobrc: str | None = None
    run_immedietly: bool

    @pyd.model_validator(mode="after")
    def check_requirements(self) -> tpe.Self:
        if self.search_wandb:
            for key in REQUIRED_ENV_VARS["wandb"]:
                if key not in self.env:
                    raise ValueError(f"Missing required environment variable {key} for wandb integration")

        for notification_type in self.notifications:
            for key in REQUIRED_ENV_VARS[notification_type]:
                if key not in self.env:
                    raise ValueError(
                        f"Missing required environment variable {key} for {notification_type} notifications"
                    )

        return self


class ServerLogsResponse(FrozenBaseModel):
    logs: str


class ServerActionResponse(FrozenBaseModel):
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


class ServerStatusResponse(FrozenBaseModel):
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    server_user: str
    server_version: str


class DiskStatsResponse(FrozenBaseModel):
    total: int
    used: int
    free: int
    percent_used: float


class NetworkStatsResponse(FrozenBaseModel):
    download_speed: float
    upload_speed: float
    ping: float


class SystemStatsResponse(FrozenBaseModel):
    cpu_percent: float
    memory_percent: float
    uptime: float
    load_avg: list[float]


class HealthResponse(FrozenBaseModel):
    status: str
    score: float
    disk: DiskStatsResponse
    network: NetworkStatsResponse
    system: SystemStatsResponse


class HeartbeatResponse(FrozenBaseModel):
    alive: bool = True
