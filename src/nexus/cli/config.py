import pathlib as pl
import typing as tp

import pydantic as pyd
import pydantic_settings as pyds
import toml

NotificationType = tp.Literal["discord", "phone"]
IntegrationType = tp.Literal["wandb", "nullpointer"]


class TargetConfig(pyd.BaseModel):
    host: str
    port: int = pyd.Field(default=54323)
    protocol: str = pyd.Field(default="https")
    api_token: str | None = None


REQUIRED_ENV_VARS = {
    "wandb": ["WANDB_API_KEY", "WANDB_ENTITY"],
    "nullpointer": [],
    "discord": ["DISCORD_USER_ID", "DISCORD_WEBHOOK_URL"],
    "phone": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PHONE_TO_NUMBER"],
}


class NexusCliConfig(pyds.BaseSettings):
    targets: dict[str, TargetConfig] = pyd.Field(default_factory=dict)
    default_target: str | None = pyd.Field(default=None)
    user: str | None = pyd.Field(default=None)
    default_integrations: list[IntegrationType] = []
    default_notifications: list[NotificationType] = []
    enable_git_tag_push: bool = pyd.Field(default=True)

    model_config = {"env_prefix": "NEXUS_", "env_nested_delimiter": "__", "extra": "ignore"}


def get_config_path() -> pl.Path:
    return pl.Path.home() / ".nexus" / "config.toml"


def get_active_target(target_name: str | None) -> tuple[str, TargetConfig | None]:
    cfg = load_config()

    if target_name:
        if target_name == "local":
            return "local", None
        if target_name not in cfg.targets:
            raise ValueError(f"Target '{target_name}' not found. Use 'nx targets list' to see available targets.")
        return target_name, cfg.targets[target_name]

    if cfg.default_target:
        if cfg.default_target == "local":
            return "local", None
        if cfg.default_target not in cfg.targets:
            raise ValueError(f"Default target '{cfg.default_target}' not found in config")
        return cfg.default_target, cfg.targets[cfg.default_target]

    return "local", None


def get_ssh_key_path(host: str, port: int) -> pl.Path:
    safe_host = host.replace(":", "_").replace("/", "_").replace("\\", "_")
    return pl.Path.home() / ".ssh" / f"nexus_{safe_host}_{port}_ed25519"


def get_server_cert_path(host: str, port: int) -> pl.Path:
    safe_host = host.replace(":", "_").replace("/", "_").replace("\\", "_")
    return pl.Path.home() / ".nexus" / f"{safe_host}_{port}_cert.pem"


def create_default_config() -> None:
    config_dir = pl.Path.home() / ".nexus"
    config_path = config_dir / "config.toml"

    # Create nexus directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        # Create default config if it doesn't exist
        config = NexusCliConfig()
        save_config(config)


def _migrate_remote_config(old_dict: dict) -> dict:
    from termcolor import colored

    if "host" not in old_dict:
        raise ValueError(f"Cannot migrate config: missing 'host' field in {old_dict}")

    host = old_dict["host"]

    if "port" not in old_dict:
        raise ValueError(f"Cannot migrate config for '{host}': missing 'port' field in {old_dict}")
    port = old_dict["port"]

    print(colored(f"Migrating config to target '{host}'", "yellow"))

    return {
        "targets": {
            host: {
                "host": host,
                "port": port,
                "protocol": old_dict.get("protocol", "https"),
                "api_token": old_dict.get("api_token"),
            }
        },
        "default_target": host,
        "user": old_dict.get("user"),
        "default_integrations": old_dict.get("default_integrations", []),
        "default_notifications": old_dict.get("default_notifications", []),
        "enable_git_tag_push": old_dict.get("enable_git_tag_push", True),
    }


def load_config() -> NexusCliConfig:
    create_default_config()
    config_path = get_config_path()

    if config_path.exists():
        try:
            with open(config_path) as f:
                config_dict = toml.load(f)

            if "host" in config_dict and config_dict.get("host") not in [None, "localhost", "127.0.0.1"]:
                config_dict = _migrate_remote_config(config_dict)

            return NexusCliConfig(**config_dict)
        except Exception as e:
            print(f"Error loading config from {config_path}: {e}")
            return NexusCliConfig()
    return NexusCliConfig()


def save_config(config: NexusCliConfig) -> None:
    import os

    config_path = get_config_path()
    config_dict = config.model_dump()

    with open(config_path, "w") as f:
        f.write("# Nexus CLI Configuration\n")
        toml.dump(config_dict, f)

    if any(target.api_token for target in config.targets.values()):
        os.chmod(config_path, 0o600)
