import os
import pathlib as pl
import typing as tp

from termcolor import colored

from nexus.cli import config, utils
from nexus.cli.config import NotificationType


def get_env_path() -> pl.Path:
    return pl.Path.home() / ".nexus" / ".env"


def create_default_env() -> None:
    env_path = get_env_path()
    env_dir = env_path.parent

    # Create nexus directory if it doesn't exist
    env_dir.mkdir(parents=True, exist_ok=True)

    if not env_path.exists():
        with open(env_path, "w") as f:
            f.write("# Nexus CLI Environment Variables\n\n")


def load_current_env() -> dict[str, str]:
    env_path = get_env_path()
    env_vars = {}

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")

    return env_vars


def save_env_vars(env_vars: dict[str, str]) -> None:
    env_path = get_env_path()

    with open(env_path, "w") as f:
        f.write("# Nexus CLI Environment Variables\n\n")
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def setup_notifications(config: config.NexusCliConfig) -> tuple[config.NexusCliConfig, dict[str, str]]:
    print(colored("\nNotification Setup", "blue", attrs=["bold"]))
    print("Nexus can notify you when your jobs complete or fail.")

    configured_notifications: list[NotificationType] = []
    env_vars = load_current_env()

    # Discord notifications
    if utils.ask_yes_no("Would you like to set up Discord notifications?"):
        configured_notifications.append("discord")
        print(colored("\nDiscord requires the following configuration:", "cyan"))

        discord_id = utils.get_user_input(
            "Your Discord user ID",
            default=env_vars.get("DISCORD_USER_ID", ""),
        )
        discord_webhook = utils.get_user_input(
            "Discord webhook URL",
            default=env_vars.get("DISCORD_WEBHOOK_URL", ""),
            required=True,
        )

        env_vars["DISCORD_USER_ID"] = discord_id
        env_vars["DISCORD_WEBHOOK_URL"] = discord_webhook

    # WhatsApp notifications
    if utils.ask_yes_no("Would you like to set up WhatsApp notifications?"):
        configured_notifications.append("whatsapp")
        print(colored("\nWhatsApp requires CallMeBot integration with the following:", "cyan"))

        callmebot_api_key = utils.get_user_input(
            "CallMeBot API Key",
            default=env_vars.get("CALLMEBOT_API_KEY", ""),
            required=True,
        )
        whatsapp_to = utils.get_user_input(
            "Your WhatsApp Number (with country code, e.g. +1234567890)",
            default=env_vars.get("WHATSAPP_TO_NUMBER", ""),
            required=True,
        )

        env_vars["CALLMEBOT_API_KEY"] = callmebot_api_key
        env_vars["WHATSAPP_TO_NUMBER"] = whatsapp_to

    # Phone notifications
    if utils.ask_yes_no("Would you like to set up Phone call notifications?"):
        configured_notifications.append("phone")
        print(colored("\nPhone call notifications - TBD implementation", "yellow"))
        print("Phone call integration is not yet implemented.")

        phone_number = utils.get_user_input(
            "Your Phone Number for future calls (with country code, e.g. +1234567890)",
            default=env_vars.get("PHONE_TO_NUMBER", ""),
            required=True,
        )

        env_vars["PHONE_TO_NUMBER"] = phone_number

    # Weights & Biases integration
    if utils.ask_yes_no("Would you like to enable Weights & Biases integration?"):
        config = config.copy(update={"search_wandb": True})
        print(colored("\nWeights & Biases requires the following:", "cyan"))

        wandb_api_key = utils.get_user_input(
            "W&B API Key",
            default=env_vars.get("WANDB_API_KEY", ""),
            required=True,
        )
        wandb_entity = utils.get_user_input(
            "W&B Entity (username or team name)",
            default=env_vars.get("WANDB_ENTITY", ""),
            required=True,
        )

        env_vars["WANDB_API_KEY"] = wandb_api_key
        env_vars["WANDB_ENTITY"] = wandb_entity

    # Set default notification types
    default_notifications: list[NotificationType] = []
    if configured_notifications:
        print(colored("\nDefault Notification Types", "blue", attrs=["bold"]))
        print("Select which notification types should be enabled by default for all jobs:")

        for notification_type in configured_notifications:
            if utils.ask_yes_no(f"Enable {notification_type} notifications by default?"):
                default_notifications.append(notification_type)

    # Additional environment variables
    if utils.ask_yes_no("Would you like to add any additional environment variables?"):
        while True:
            key = utils.get_user_input("Variable name (or press Enter to finish)")
            if not key:
                break

            value = utils.get_user_input(f"Value for {key}", required=True)
            env_vars[key] = value

    config = config.copy(update={"default_notifications": default_notifications})
    return config, env_vars


def setup_non_interactive() -> None:
    """Set up Nexus configuration non-interactively using environment variables."""
    # Load or create default config
    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    # Environment variables are loaded automatically via pydantic_settings

    # Load current environment variable file if exists
    env_vars = load_current_env()

    # Save the environment variables that are already in the .env file
    create_default_env()
    save_env_vars(env_vars)

    # Save configuration
    config.save_config(cfg)

    print(colored("Non-interactive setup complete!", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
    print(f"Environment variables saved to: {get_env_path()}")


def setup_wizard() -> None:
    print(colored("Nexus CLI Setup Wizard", "blue", attrs=["bold"]))
    print("Let's set up your Nexus CLI configuration.")

    # Load or create default config
    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    # Basic configuration
    print(colored("\nBasic Configuration", "blue", attrs=["bold"]))

    host = utils.get_user_input("Nexus API host", default=cfg.host)
    port = utils.get_user_input("Nexus API port", default=str(cfg.port))
    user = utils.get_user_input("Your username", default=cfg.user or os.environ.get("USER", ""))

    cfg = tp.cast(
        config.NexusCliConfig,
        cfg.copy(
            update={
                "host": host,
                "port": int(port),
                "user": user,
            }
        ),
    )

    # Notification setup
    cfg, env_vars = setup_notifications(cfg)

    # Debug config values
    print("\nDebug - Config values before saving:")
    print(f"search_wandb: {cfg.search_wandb}")
    print(f"default_notifications: {cfg.default_notifications}")

    # Save configuration
    config.save_config(cfg)

    # Save environment variables
    create_default_env()
    save_env_vars(env_vars)

    print(colored("\nSetup complete!", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
    print(f"Environment variables saved to: {get_env_path()}")
    print("\nYou can edit these files at any time with:")
    print("  nexus config    # Edit configuration")
    print("  nexus env       # Edit environment variables")


def open_config_editor() -> None:
    config_path = config.get_config_path()
    if not config_path.exists():
        config.create_default_config()

    utils.open_file_in_editor(config_path)


def open_env_editor() -> None:
    env_path = get_env_path()
    if not env_path.exists():
        create_default_env()

    utils.open_file_in_editor(env_path)


def check_config_exists() -> bool:
    config_path = config.get_config_path()
    return config_path.exists() and config_path.stat().st_size > 0
