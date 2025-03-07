import pydantic as pyd
import typing_extensions as tpe

from nexus.server.core import schemas

__all__ = [
    "JobRequest",
    "JobUpdateRequest",
    "JobListRequest",
    "ServerLogsResponse",
    "JobLogsResponse",
    "GpuActionResponse",
    "GpuStatusResponse",
    "ServerStatusResponse",
    "ServerActionResponse",
    "HealthResponse",
]

REQUIRED_ENV_VARS = {
    "wandb": ["WANDB_API_KEY", "WANDB_ENTITY"],
    "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"],
    "phone": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PHONE_TO_NUMBER"],
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
    gpu_idxs: list[int] | None = None
    priority: int = 0
    search_wandb: bool = False
    notifications: list[schemas.NotificationType] = []
    env: dict[str, str] = {}
    jobrc: str | None = None
    run_immediately: bool = False

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


class JobLogsResponse(FrozenBaseModel):
    logs: str


class GpuActionError(FrozenBaseModel):
    index: int
    error: str


class GpuActionResponse(FrozenBaseModel):
    blacklisted: list[int] | None = None
    removed: list[int] | None = None
    failed: list[GpuActionError]


class GpuStatusResponse(FrozenBaseModel):
    gpu_idx: int
    blacklisted: bool
    changed: bool


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
    alive: bool = True
    status: str | None = None
    score: float | None = None
    disk: DiskStatsResponse | None = None
    network: NetworkStatsResponse | None = None
    system: SystemStatsResponse | None = None


class ServerActionResponse(FrozenBaseModel):
    status: str


class JobUpdateRequest(FrozenBaseModel):
    command: str | None = None
    priority: int | None = None


class JobListRequest(FrozenBaseModel):
    status: schemas.JobStatus | None = None
    gpu_index: int | None = None
    command_regex: str | None = None
    limit: int = 100
    offset: int = 0
