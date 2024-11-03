import pathlib
import dotenv
import typing

import pydantic as pyd
import pydantic_settings as pyds


class NexusConfig(pyds.BaseSettings):
    log_dir: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / "logs")
    state_path: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / "state.json")
    repo_dir: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / "repos")
    env_file: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / ".env")
    refresh_rate: int = pyd.Field(default=5)
    history_limit: int = pyd.Field(default=1000)
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54322)

    model_config = pyds.SettingsConfigDict(
        env_file=str(pathlib.Path.home() / ".nexus" / ".env"),
        env_file_encoding="utf-8",
        extra="allow",  # Allow extra fields from .env
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
        return (init_settings, env_settings, dotenv_settings, pyds.TomlConfigSettingsSource(settings_cls))


DEFAULT_ENV_TEMPLATE = """# Nexus Environment Configuration
"""


def load_config() -> NexusConfig:
    """Load configuration, creating default if it doesn't exist."""
    config_dir = pathlib.Path.home() / ".nexus"
    config_path = config_dir / "config.toml"
    env_path = config_dir / ".env"

    # Create nexus directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    # Create default .env if it doesn't exist
    if not env_path.exists():
        env_path.write_text(DEFAULT_ENV_TEMPLATE)
        print(f"Created default .env at {env_path}")
        print("Please edit it to add your git credentials and other environment variables.")

    if not config_path.exists():
        # Create default config if it doesn't exist
        config = NexusConfig()
        # Write default config
        with open(config_path, "w") as f:
            f.write(f"""# Nexus Configuration
log_dir = "{config.log_dir}"
state_path = "{config.state_path}"
repo_dir = "{config.repo_dir}"
refresh_rate = {config.refresh_rate}
host = "{config.host}"
port = {config.port}
""")

    config = NexusConfig()

    # Ensure directories exist
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.repo_dir.mkdir(parents=True, exist_ok=True)

    if config.state_path.suffix:  # If it's a file path (has extension)
        config.state_path.parent.mkdir(parents=True, exist_ok=True)
        config.state_path.touch(exist_ok=True)

    # Load environment variables
    dotenv.load_dotenv(config.env_file)

    return config
