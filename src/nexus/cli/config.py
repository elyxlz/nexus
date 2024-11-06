import pathlib
import typing

import pydantic as pyd
import pydantic_settings as pyds


class NexusCliConfig(pyds.BaseSettings):
    host: str = pyd.Field(default="localhost")
    port: int = pyd.Field(default=54323)
    user: str | None = pyd.Field(default=None)
    discord_id: str | None = pyd.Field(default=None)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: typing.Type[pyds.BaseSettings],
        init_settings: pyds.PydanticBaseSettingsSource,
        env_settings: pyds.PydanticBaseSettingsSource,
        dotenv_settings: pyds.PydanticBaseSettingsSource,
        file_secret_settings: pyds.PydanticBaseSettingsSource,
    ) -> tuple[pyds.PydanticBaseSettingsSource, ...]:
        return (init_settings, pyds.TomlConfigSettingsSource(settings_cls, toml_file=pathlib.Path.home() / ".nexus" / "config.toml"))


def create_default_config() -> None:
    """Create default configuration files if they don't exist."""
    config_dir = pathlib.Path.home() / ".nexus"
    config_path = config_dir / "config.toml"

    # Create nexus directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        # Create default config if it doesn't exist
        config = NexusCliConfig()
        # Write default config
        with open(config_path, "w") as f:
            f.write(f"""# Nexus CLI Configuration
host = "{config.host}"
port = {config.port}
user = ""  # Your username
discord_id = ""  # Your Discord user ID for notifications
""")


def load_config() -> NexusCliConfig:
    """Load CLI configuration."""
    create_default_config()
    return NexusCliConfig()
