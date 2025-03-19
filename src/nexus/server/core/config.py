import json
import pathlib as pl

import pydantic as pyd
import pydantic_settings as pyds
import toml

__all__ = [
    "NexusServerConfig",
    "get_env_path",
    "get_config_path",
    "get_db_path",
    "save_config",
    "load_config",
]


class NexusServerConfig(pyds.BaseSettings):
    model_config = pyds.SettingsConfigDict(env_prefix="ns_", frozen=True, extra="ignore")

    server_dir: pl.Path | None  # if none, never persist
    refresh_rate: int = pyd.Field(default=3)
    port: int = pyd.Field(default=54323)
    node_name: str = pyd.Field(default="test_node")
    log_level: str = pyd.Field(default="info")
    mock_gpus: bool = pyd.Field(default=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[pyds.BaseSettings],
        init_settings: pyds.PydanticBaseSettingsSource,
        env_settings: pyds.PydanticBaseSettingsSource,
        dotenv_settings: pyds.PydanticBaseSettingsSource,
        file_secret_settings: pyds.PydanticBaseSettingsSource,
    ) -> tuple[pyds.PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings


def get_env_path(server_dir: pl.Path) -> pl.Path:
    return server_dir / ".env"


def get_config_path(server_dir: pl.Path) -> pl.Path:
    return server_dir / "config.toml"


def get_db_path(server_dir: pl.Path) -> pl.Path:
    return server_dir / "nexus_server.db"


def save_config(config: NexusServerConfig) -> None:
    assert config.server_dir is not None
    config_dict = json.loads(config.model_dump_json())
    with get_config_path(config.server_dir).open("w") as f:
        toml.dump(config_dict, f)


def load_config(server_dir: pl.Path) -> NexusServerConfig:
    config_file = get_config_path(server_dir)
    config_data = toml.load(config_file)
    config_data["server_dir"] = server_dir
    return NexusServerConfig(**config_data)
