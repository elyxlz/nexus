import typing as tp

import pydantic as pyd
import typing_extensions as tpe

__all__ = [
    "JobRequest",
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

NotificationType = tp.Literal["discord"]  # TODO: whatsapp, phone call

REQUIRED_ENV_VARS = {"wandb": ["WANDB_API_KEY", "WANDB_ENTITY"], "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"]}


class FrozenBaseModel(pyd.BaseModel):
    model_config = pyd.ConfigDict(frozen=True)


class JobRequest(FrozenBaseModel):
    command: str
    git_repo_url: str
    git_tag: str
    git_branch: str
    user: str
    search_wandb: bool = False
    notifications: list[NotificationType] = []
    environment: dict[str, str] = {}
    jobrc: str | None = None

    @pyd.model_validator(mode="after")
    def check_requirements(self) -> tpe.Self:
        if self.search_wandb:
            for key in REQUIRED_ENV_VARS["wandb"]:
                if key not in self.environment:
                    raise ValueError(f"Missing required environment variable {key} for wandb integration")

        for notification_type in self.notifications:
            for key in REQUIRED_ENV_VARS[notification_type]:
                if key not in self.environment:
                    raise ValueError(
                        f"Missing required environment variable {key} for {notification_type} notifications"
                    )

        return self


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
    gpu_count: int
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    service_user: str
    service_version: str
