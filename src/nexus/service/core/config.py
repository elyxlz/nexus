import json
import pathlib as pl
import typing

import pydantic as pyd
import pydantic_settings as pyds
import toml


class NexusServiceConfig(pyds.BaseSettings):
    model_config = pyds.SettingsConfigDict(env_prefix="ns_", frozen=True)

    service_dir: pl.Path | None  # if none, never persist
    refresh_rate: int = pyd.Field(default=3)
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54323)
    webhooks_enabled: bool = pyd.Field(default=False)
    node_name: str | None = pyd.Field(default=None)
    log_level: typing.Literal["info", "debug"] = pyd.Field(default="info")
    mock_gpus: bool = pyd.Field(default=False)


def get_env_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / ".env"


def get_config_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / "config.toml"


def get_db_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / "nexus_service.db"


def get_jobs_dir(service_dir: pl.Path) -> pl.Path:
    return service_dir / "jobs"


def save_config(config: NexusServiceConfig) -> None:
    assert config.service_dir is not None
    config_dict = json.loads(config.model_dump_json())
    with get_config_path(config.service_dir).open("w") as f:
        toml.dump(config_dict, f)


def load_config(service_dir: pl.Path) -> NexusServiceConfig:
    config_file = get_config_path(service_dir)
    config_data = toml.load(config_file)
    config_data["service_dir"] = service_dir
    return NexusServiceConfig(**config_data)
