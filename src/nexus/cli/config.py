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
    ssh_user: str


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

    print(
        colored(
            "\nOld config format detected. Remote targets with API tokens are no longer supported.\n"
            "Please reconfigure remotes with: nx target add\n",
            "yellow",
        )
    )

    return {
        "targets": {},
        "default_target": None,
        "user": old_dict.get("user"),
        "default_integrations": old_dict.get("default_integrations", []),
        "default_notifications": old_dict.get("default_notifications", []),
        "enable_git_tag_push": old_dict.get("enable_git_tag_push", True),
    }


def _validate_and_migrate_targets(config_dict: dict) -> dict:
    from termcolor import colored

    if "targets" not in config_dict:
        return config_dict

    valid_targets = {}
    invalid_targets = []

    for name, target_data in config_dict["targets"].items():
        if "api_token" in target_data or "protocol" in target_data:
            invalid_targets.append(name)
        elif "ssh_user" not in target_data:
            invalid_targets.append(name)
        else:
            valid_targets[name] = target_data

    if invalid_targets:
        print(
            colored(
                f"\nRemoved invalid targets (old format with API tokens): {', '.join(invalid_targets)}\n"
                "Please reconfigure these remotes with: nx target add\n",
                "yellow",
            )
        )
        config_dict["targets"] = valid_targets

        if config_dict.get("default_target") in invalid_targets:
            config_dict["default_target"] = None

    return config_dict


def load_config() -> NexusCliConfig:
    create_default_config()
    config_path = get_config_path()

    if config_path.exists():
        try:
            with open(config_path) as f:
                config_dict = toml.load(f)

            if "host" in config_dict and config_dict.get("host") not in [None, "localhost", "127.0.0.1"]:
                config_dict = _migrate_remote_config(config_dict)

            config_dict = _validate_and_migrate_targets(config_dict)

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
