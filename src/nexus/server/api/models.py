import pydantic as pyd
import typing as tp
import typing_extensions as tpe

from nexus.server.core import schemas

__all__ = [
    "SingleFieldResponse",
    "JobRequest",
    "JobUpdateRequest",
    "JobListRequest",
    "JobLogsResponse",
    "ServerLogsResponse",
    "ServerActionResponse",
    "GpuActionResponse",
    "GpuStatusResponse",
    "ServerStatusResponse",
    "HealthResponse",
    "ArtifactUploadResponse",
    "ArtifactCheckResponse",
]

REQUIRED_ENV_VARS = {
    "wandb": ["WANDB_API_KEY", "WANDB_ENTITY"],
    "nullpointer": [],
    "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"],
    "phone": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PHONE_TO_NUMBER"],
}


class FrozenBaseModel(pyd.BaseModel):
    model_config = pyd.ConfigDict(frozen=True)


T = tp.TypeVar("T")


class SingleFieldResponse(FrozenBaseModel, tp.Generic[T]):
    data: T


JobLogsResponse = SingleFieldResponse[str]
ServerLogsResponse = SingleFieldResponse[str]
ServerActionResponse = SingleFieldResponse[str]
ArtifactUploadResponse = SingleFieldResponse[str]


class ArtifactCheckResponse(FrozenBaseModel):
    exists: bool
    artifact_id: str | None = None


def _check_required_vars(kinds: tp.Sequence[str], env: dict[str, str], for_type: str = "") -> None:
    for kind in kinds:
        for key in REQUIRED_ENV_VARS[kind]:
            if key not in env:
                suffix = f" for {kind} {for_type}" if for_type else f" for {kind}"
                raise ValueError(f"Missing required environment variable {key}{suffix}")


class JobRequest(FrozenBaseModel):
    job_id: str | None = None
    artifact_id: str
    command: str
    user: str
    num_gpus: int = 1
    gpu_idxs: list[int] | None = None
    priority: int = 0
    integrations: list[schemas.IntegrationType] = []
    notifications: list[schemas.NotificationType] = []
    env: dict[str, str] = {}
    jobrc: str | None = None
    run_immediately: bool = False
    ignore_blacklist: bool = False
    git_repo_url: str | None = None
    git_branch: str | None = None
    git_tag: str | None = None

    @pyd.model_validator(mode="after")
    def check_requirements(self) -> tpe.Self:
        _check_required_vars(self.integrations, self.env, "integration")
        _check_required_vars(self.notifications, self.env, "notifications")
        return self


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
    node_name: str


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


class JobUpdateRequest(FrozenBaseModel):
    command: str | None = None
    priority: int | None = None
    num_gpus: int | None = None
    git_tag: str | None = None


class JobListRequest(FrozenBaseModel):
    status: schemas.JobStatus | None = None
    gpu_index: int | None = None
    command_regex: str | None = None
    limit: int = 1000
    offset: int = 0
