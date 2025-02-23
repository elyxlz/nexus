import json
import pathlib as pl
import typing

import pydantic as pyd
import pydantic_settings as pyds
import toml


def get_env_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / ".env"


def get_config_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / "config.toml"


def get_state_path(service_dir: pl.Path) -> pl.Path:
    return service_dir / "state.json"


def get_jobs_dir(service_dir: pl.Path) -> pl.Path:
    return service_dir / "jobs"


class NexusServiceEnv(pyds.BaseSettings):
    github_token: str = pyd.Field(default="")
    discord_webhook_url: str = pyd.Field(default="")
    wandb_api_key: str = pyd.Field(default="")
    wandb_entity: str = pyd.Field(default="")

    model_config = pyds.SettingsConfigDict(frozen=True, env_file_encoding="utf-8", case_sensitive=False, extra="ignore")


class NexusServiceConfig(pyds.BaseSettings):
    model_config = pyds.SettingsConfigDict(env_prefix="ns_", frozen=True)

    service_dir: pl.Path = pyd.Field(default_factory=lambda: pl.Path.home() / ".nexus_service")
    refresh_rate: int = pyd.Field(default=5)
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54323)
    webhooks_enabled: bool = pyd.Field(default=False)
    node_name: str | None = pyd.Field(default=None)
    log_level: typing.Literal["info", "debug"] = pyd.Field(default="info")
    mock_gpus: bool = pyd.Field(default=False)
    persist_to_disk: bool = pyd.Field(default=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[pyds.BaseSettings],
        init_settings: pyds.PydanticBaseSettingsSource,
        env_settings: pyds.PydanticBaseSettingsSource,
        dotenv_settings: pyds.PydanticBaseSettingsSource,
        file_secret_settings: pyds.PydanticBaseSettingsSource,
    ) -> tuple[pyds.PydanticBaseSettingsSource, ...]:
        field = cls.model_fields["service_dir"]
        default_service_dir = field.default_factory() if field.default_factory is not None else field.default
        return (
            init_settings,
            pyds.TomlConfigSettingsSource(settings_cls, toml_file=get_config_path(default_service_dir)),
            env_settings,
        )


def save_config(config: NexusServiceConfig) -> None:
    config_dict = json.loads(config.model_dump_json())
    with get_config_path(config.service_dir).open("w") as f:
        toml.dump(config_dict, f)


def save_env(env: NexusServiceEnv, env_path: pl.Path) -> None:
    env_dict = env.model_dump()
    with env_path.open("w", encoding="utf-8") as f:
        for key, value in env_dict.items():
            f.write(f"{key.upper()}={value}\n")
