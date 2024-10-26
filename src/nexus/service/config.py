import pathlib
import typing

import pydantic as pyd
import pydantic_settings as pyds


class NexusConfig(pyds.BaseSettings):
    log_dir: pathlib.Path = pyd.Field(
        default_factory=lambda: pathlib.Path.home() / ".nexus" / "logs"
    )
    state_path: pathlib.Path = pyd.Field(
        default_factory=lambda: pathlib.Path.home() / ".nexus" / "state.json"
    )
    refresh_rate: int = pyd.Field(default=10)
    history_limit: int = pyd.Field(default=1000)
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54322)

    model_config = pyds.SettingsConfigDict(
        toml_file=str(pathlib.Path.home() / ".nexus" / "config.toml")
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: typing.Type[pyds.BaseSettings],
        init_settings: pyds.PydanticBaseSettingsSource,
        env_settings: pyds.PydanticBaseSettingsSource,
        dotenv_settings: pyds.PydanticBaseSettingsSource,
        file_secret_settings: pyds.PydanticBaseSettingsSource,
    ) -> tuple[pyds.PydanticBaseSettingsSource, ...]:
        return (init_settings, pyds.TomlConfigSettingsSource(settings_cls))


def load_config() -> NexusConfig:
    """Load configuration, creating default if it doesn't exist."""
    config_path = pathlib.Path.home() / ".nexus" / "config.toml"

    if not config_path.exists():
        # Create default config if it doesn't exist
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = NexusConfig()
        # Write default config
        with open(config_path, "w") as f:
            f.write(f"""# Nexus Configuration
log_dir = "{config.log_dir}"
state_path = "{config.state_path}"
refresh_rate = {config.refresh_rate}
host = "{config.host}"
port = {config.port}
""")

    config = NexusConfig()

    # Ensure directories and files exist
    config.log_dir.mkdir(parents=True, exist_ok=True)
    if config.state_path.suffix:  # If it's a file path (has extension)
        config.state_path.parent.mkdir(parents=True, exist_ok=True)
        config.state_path.touch(exist_ok=True)

    return config
