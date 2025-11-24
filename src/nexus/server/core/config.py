import json
import pathlib as pl
import socket

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
    "get_default_node_name",
]


def get_default_node_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "nexus-node"


def get_external_ip() -> str | None:
    import subprocess

    try:
        result = subprocess.run(["curl", "-s", "ifconfig.me"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


class NexusServerConfig(pyds.BaseSettings):
    model_config = pyds.SettingsConfigDict(env_prefix="ns_", frozen=True, extra="ignore")

    server_dir: pl.Path | None
    refresh_rate: int = pyd.Field(default=3)
    port: int = pyd.Field(default=54323)
    node_name: str = pyd.Field(default_factory=get_default_node_name)
    mock_gpus: bool = pyd.Field(default=False)
    supplementary_groups: list[str] = pyd.Field(default_factory=list)
    external_ip: str | None = pyd.Field(default_factory=get_external_ip)

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
