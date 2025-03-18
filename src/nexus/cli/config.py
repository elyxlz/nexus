import pathlib as pl
import typing as tp

import pydantic as pyd
import pydantic_settings as pyds
import toml

NotificationType = tp.Literal["discord", "phone"]


REQUIRED_ENV_VARS = {
    "wandb": ["WANDB_API_KEY", "WANDB_ENTITY"],
    "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"],
    "phone": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PHONE_TO_NUMBER"],
}


class NexusCliConfig(pyds.BaseSettings):
    port: int = pyd.Field(default=54323)
    user: str | None = pyd.Field(default=None)
    search_wandb: bool = False
    default_notifications: list[NotificationType] = []

    model_config = {"env_prefix": "NEXUS_", "env_nested_delimiter": "__", "extra": "ignore"}


def get_config_path() -> pl.Path:
    return pl.Path.home() / ".nexus" / "config.toml"


def create_default_config() -> None:
    config_dir = pl.Path.home() / ".nexus"
    config_path = config_dir / "config.toml"

    # Create nexus directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        # Create default config if it doesn't exist
        config = NexusCliConfig()
        save_config(config)


def load_config() -> NexusCliConfig:
    create_default_config()
    config_path = get_config_path()

    if config_path.exists():
        try:
            with open(config_path) as f:
                config_dict = toml.load(f)
            return NexusCliConfig(**config_dict)
        except Exception as e:
            print(f"Error loading config from {config_path}: {e}")
            return NexusCliConfig()
    return NexusCliConfig()


def save_config(config: NexusCliConfig) -> None:
    config_path = get_config_path()
    config_dict = config.model_dump()

    with open(config_path, "w") as f:
        f.write("# Nexus CLI Configuration\n")
        toml.dump(config_dict, f)
