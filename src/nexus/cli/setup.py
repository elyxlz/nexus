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

    env_dir.mkdir(parents=True, exist_ok=True)

    if not env_path.exists():
        with open(env_path, "w") as f:
            f.write("# Nexus CLI Environment Variables\n\n")


def read_env_file(env_path: pl.Path) -> dict[str, str]:
    env_vars = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def load_current_env() -> dict[str, str]:
    global_env_path = get_env_path()
    env_vars = read_env_file(global_env_path)
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

    if utils.ask_yes_no("Would you like to set up Phone Call notifications?", default=False):
        configured_notifications.append("phone")
        print(colored("\nPhone Calls using Twilio require the following:", "cyan"))

        twilio_account_sid = utils.get_user_input(
            "Twilio Account SID",
            default=env_vars.get("TWILIO_ACCOUNT_SID", ""),
            required=True,
        )
        twilio_auth_token = utils.get_user_input(
            "Twilio Auth Token",
            default=env_vars.get("TWILIO_AUTH_TOKEN", ""),
            required=True,
        )
        twilio_from_number = utils.get_user_input(
            "Twilio Phone Number (with country code, e.g. +1234567890)",
            default=env_vars.get("TWILIO_FROM_NUMBER", ""),
            required=True,
        )
        phone_to = utils.get_user_input(
            "Your Phone Number to receive calls (with country code, e.g. +1234567890)",
            default=env_vars.get("PHONE_TO_NUMBER", ""),
            required=True,
        )

        env_vars["TWILIO_ACCOUNT_SID"] = twilio_account_sid
        env_vars["TWILIO_AUTH_TOKEN"] = twilio_auth_token
        env_vars["TWILIO_FROM_NUMBER"] = twilio_from_number
        env_vars["PHONE_TO_NUMBER"] = phone_to

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

    default_notifications: list[NotificationType] = []
    if configured_notifications:
        print(colored("\nDefault Notification Types", "blue", attrs=["bold"]))
        print("Select which notification types should be enabled by default for all jobs:")

        for notification_type in configured_notifications:
            if utils.ask_yes_no(f"Enable {notification_type} notifications by default?"):
                default_notifications.append(notification_type)

    # Add Git token for private repositories
    if utils.ask_yes_no("Do you work with private Git repositories?", default=False):
        print(colored("\nNexus needs a Git token to clone private repositories:", "cyan"))
        print("You can create a Personal Access Token at: https://github.com/settings/tokens")
        print("The token needs 'repo' scope to access private repositories")

        git_token = utils.get_user_input(
            "Git Personal Access Token",
            default=env_vars.get("GIT_TOKEN", ""),
            required=True,
        )
        env_vars["GIT_TOKEN"] = git_token

    if utils.ask_yes_no("Would you like to add any additional environment variables?", default=True):
        # Save current env vars before opening editor
        create_default_env()
        save_env_vars(env_vars)
        # Open editor for user to add variables directly
        utils.open_file_in_editor(get_env_path())
        # Reload env vars after editing
        env_vars = load_current_env()

    config = config.copy(update={"default_notifications": default_notifications})
    return config, env_vars


def setup_non_interactive() -> None:
    """Set up Nexus configuration non-interactively using environment variables."""
    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    env_vars = load_current_env()

    create_default_env()
    save_env_vars(env_vars)

    config.save_config(cfg)

    print(colored("Non-interactive setup complete!", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
    print(f"Environment variables saved to: {get_env_path()}")


def setup_wizard() -> None:
    print(colored("Nexus CLI Setup Wizard", "blue", attrs=["bold"]))
    print("Let's set up your Nexus CLI configuration.")

    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    print(colored("\nBasic Configuration", "blue", attrs=["bold"]))
    print(colored("Press ENTER to accept the default values shown in cyan.", "white"))

    port = utils.get_user_input("Nexus API port", default=str(cfg.port))
    user = utils.get_user_input("Your username", default=cfg.user or os.environ.get("USER", ""))

    cfg = tp.cast(
        config.NexusCliConfig,
        cfg.copy(
            update={
                "port": int(port),
                "user": user,
            }
        ),
    )

    cfg, env_vars = setup_notifications(cfg)

    # Initialize jobrc file if requested
    if utils.ask_yes_no("Would you like to set up a job runtime configuration (.jobrc)?"):
        jobrc_path = get_jobrc_path()
        create_default_jobrc()
        utils.open_file_in_editor(jobrc_path)

    print("\nDebug - Config values before saving:")
    print(f"search_wandb: {cfg.search_wandb}")
    print(f"default_notifications: {cfg.default_notifications}")

    config.save_config(cfg)

    create_default_env()
    save_env_vars(env_vars)

    print(colored("\nSetup complete!", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
    print(f"Environment variables saved to: {get_env_path()}")
    print("\nYou can edit these files at any time with:")
    print("  nexus config    # Edit configuration")
    print("  nexus env       # Edit environment variables")
    print("  nexus jobrc     # Manage job runtime configuration")


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


def get_jobrc_path() -> pl.Path:
    return pl.Path.home() / ".nexus" / ".jobrc"


def create_default_jobrc() -> None:
    jobrc_path = get_jobrc_path()
    jobrc_path.parent.mkdir(parents=True, exist_ok=True)
    if not jobrc_path.exists():
        jobrc_path.touch()


def open_jobrc_editor() -> None:
    jobrc_path = get_jobrc_path()
    if not jobrc_path.exists():
        create_default_jobrc()

    utils.open_file_in_editor(jobrc_path)


def check_config_exists() -> bool:
    config_path = config.get_config_path()
    return config_path.exists() and config_path.stat().st_size > 0
