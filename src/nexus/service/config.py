import pathlib
import typing

import dotenv
import pydantic as pyd
import pydantic_settings as pyds


class NexusServiceConfig(pyds.BaseSettings):
    jobs_dir: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / "jobs")
    state_path: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / "state.json")
    env_file: pathlib.Path = pyd.Field(default_factory=lambda: pathlib.Path.home() / ".nexus" / ".env")
    refresh_rate: int = pyd.Field(default=5)
    history_limit: int = pyd.Field(default=1000)
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54322)
    webhooks_enabled: bool = pyd.Field(default=True)

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


def create_default_config() -> None:
    """Create default configuration files if they don't exist."""
    config_dir = pathlib.Path.home() / ".nexus"
    config_path = config_dir / "config.toml"
    env_path = config_dir / ".env"

    # Create nexus directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    DEFAULT_ENV_TEMPLATE = """# Nexus Service Environment Configuration
    NEXUS_DISCORD_WEBHOOK_URL=
    """

    # Create default .env if it doesn't exist
    if not env_path.exists():
        env_path.write_text(DEFAULT_ENV_TEMPLATE)

    if not config_path.exists():
        # Create default config if it doesn't exist
        config = NexusServiceConfig()
        # Write default config
        with open(config_path, "w") as f:
            f.write(f"""# Nexus Service Configuration
jobs_dir = "{config.jobs_dir}"
state_path = "{config.state_path}"
env_file = "{config.env_file}"
refresh_rate = {config.refresh_rate}
host = "{config.host}"
port = "{config.port}"
webhooks_enabled = "{config.webhooks_enabled}"
""")


def load_config() -> NexusServiceConfig:
    """Load configuration."""
    create_default_config()

    config = NexusServiceConfig()

    # Ensure directories exist
    config.jobs_dir.mkdir(parents=True, exist_ok=True)

    # Load environment variables
    dotenv.load_dotenv(config.env_file)

    return config
