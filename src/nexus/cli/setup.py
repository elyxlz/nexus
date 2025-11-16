import os
import pathlib as pl
import typing as tp

from dotenv import dotenv_values
from termcolor import colored

from nexus.cli import config, utils
from nexus.cli.config import IntegrationType, NotificationType


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


def load_local_env(local_path: pl.Path | None = None) -> dict[str, str]:
    if local_path is None:
        local_path = pl.Path.cwd() / ".env"

    if not local_path.exists():
        return {}

    env_dict = dotenv_values(local_path)
    return {k: v for k, v in env_dict.items() if v is not None}


def merge_env_with_conflicts(
    global_env: dict[str, str], local_env: dict[str, str]
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    conflicts: dict[str, tuple[str, str]] = {}
    merged = global_env.copy()

    for key, local_value in local_env.items():
        if key in global_env and global_env[key] != local_value:
            conflicts[key] = (global_env[key], local_value)
        merged[key] = local_value

    return merged, conflicts


def save_env_vars(env_vars: dict[str, str]) -> None:
    env_path = get_env_path()

    with open(env_path, "w") as f:
        f.write("# Nexus CLI Environment Variables\n\n")
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def setup_notifications(cfg: config.NexusCliConfig) -> tuple[config.NexusCliConfig, dict[str, str]]:
    print(colored("\nNotification and Integration Setup", "blue", attrs=["bold"]))
    print("Nexus can notify you when your jobs complete or fail, and integrate with various services.")

    configured_notifications: list[NotificationType] = []
    configured_integrations: list[IntegrationType] = []
    env_vars = load_current_env()

    print(colored("\nNotification Setup", "blue", attrs=["bold"]))
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

    print(colored("\nIntegration Setup", "blue", attrs=["bold"]))
    if utils.ask_yes_no("Would you like to enable Weights & Biases integration?"):
        configured_integrations.append("wandb")
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

    if utils.ask_yes_no("Would you like to enable nullpointer (0x0.st) integration for logs?", default=False):
        configured_integrations.append("nullpointer")
        print(colored("\nNullpointer integration will upload logs to 0x0.st with long, unlisted URLs", "cyan"))
        print(colored("and set them to expire after 24 hours", "cyan"))
        print(colored("\nWarning: While logs are unlisted and temporary, they are still publicly accessible", "yellow"))
        print(colored("Be careful with sensitive information in your logs", "yellow"))

    default_notifications: list[NotificationType] = []
    if configured_notifications:
        print(colored("\nDefault Notification Types", "blue", attrs=["bold"]))
        print("Select which notification types should be enabled by default for all jobs:")

        for notification_type in configured_notifications:
            if utils.ask_yes_no(f"Enable {notification_type} notifications by default?"):
                default_notifications.append(notification_type)

        updated_config = cfg.copy(update={"default_notifications": default_notifications})
        config.save_config(updated_config)

    default_integrations: list[IntegrationType] = []
    if configured_integrations:
        print(colored("\nDefault Integration Types", "blue", attrs=["bold"]))
        print("Select which integration types should be enabled by default for all jobs:")

        for integration_type in configured_integrations:
            if utils.ask_yes_no(f"Enable {integration_type} integration by default?"):
                default_integrations.append(integration_type)

        updated_config = cfg.copy(update={"default_integrations": default_integrations})
        config.save_config(updated_config)

    if utils.ask_yes_no("Would you like to add any additional environment variables?", default=True):
        create_default_env()
        save_env_vars(env_vars)
        utils.open_file_in_editor(get_env_path())
        env_vars = load_current_env()

    cfg = cfg.copy(
        update={"default_notifications": default_notifications, "default_integrations": default_integrations}
    )
    return cfg, env_vars


def setup_non_interactive() -> None:
    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    config.save_config(cfg)

    env_vars = load_current_env()

    create_default_env()
    save_env_vars(env_vars)

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

    user = utils.get_user_input("Your username", default=cfg.user or os.environ.get("USER", ""))

    cfg = tp.cast(
        config.NexusCliConfig,
        cfg.copy(
            update={
                "user": user,
            }
        ),
    )

    config.save_config(cfg)

    cfg, env_vars = setup_notifications(cfg)

    if utils.ask_yes_no("Would you like to set up a job runtime configuration (.jobrc)?"):
        jobrc_path = get_jobrc_path()
        create_default_jobrc()
        utils.open_file_in_editor(jobrc_path)

    print("\nDebug - Config values before saving:")
    print(f"default_integrations: {cfg.default_integrations}")
    print(f"default_notifications: {cfg.default_notifications}")

    config.save_config(cfg)

    create_default_env()
    save_env_vars(env_vars)

    print(colored("\nSetup complete!", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
    print(f"Environment variables saved to: {get_env_path()}")
    print("\nYou can edit these files at any time with:")
    print("  nexus config")
    print("  nexus env")
    print("  nexus jobrc")

    if utils.ask_yes_no("\nWould you like to add remote Nexus servers?", default=False):
        while True:
            add_target()
            if not utils.ask_yes_no("\nAdd another remote server?", default=False):
                break


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


def _test_ssh_connection(host: str, ssh_user: str) -> bool:
    import subprocess

    print(colored("\nTesting SSH connection...", "cyan"))
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "BatchMode=yes",
                f"{ssh_user}@{host}",
                "echo",
                "ok",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            print(colored("✓ SSH connection successful", "green"))
            return True
        else:
            print(colored(f"✗ SSH connection failed: {result.stderr.strip()}", "red"))
            return False
    except subprocess.TimeoutExpired:
        print(colored("✗ SSH connection timed out", "red"))
        return False
    except Exception as e:
        print(colored(f"✗ SSH connection error: {e}", "red"))
        return False


def _test_tunnel_and_server(target_name: str) -> tuple[bool, str | None]:
    import requests

    from nexus.cli.tunnel_manager import SSHTunnelError, get_or_create_tunnel

    print(colored("Testing SSH tunnel and server connection...", "cyan"))
    try:
        local_port = get_or_create_tunnel(target_name)
        url = f"http://127.0.0.1:{local_port}/v1/server/status"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        status = response.json()
        server_name = status.get("node_name")
        if not server_name:
            print(colored("✗ Server did not return node_name", "red"))
            return False, None
        print(colored(f"✓ Connected to server: {server_name}", "green"))
        return True, server_name
    except SSHTunnelError as e:
        print(colored(f"✗ {e}", "red"))
        return False, None
    except requests.RequestException as e:
        print(colored(f"✗ Server connection failed: {e}", "red"))
        print(colored("\nPossible causes:", "yellow"))
        print(colored("  • Server is not running", "yellow"))
        print(colored("  • Server is not listening on the specified port", "yellow"))
        return False, None
    except Exception as e:
        print(colored(f"✗ Connection test failed: {e}", "red"))
        return False, None


def add_target() -> None:
    print(colored("\nAdd Target Server", "blue", attrs=["bold"]))
    print("Configure CLI to connect to a remote Nexus server via SSH tunnel.")
    print(colored("\nPrerequisites:", "yellow"))
    print("  • Your SSH public key must be in ~/.ssh/authorized_keys on the remote host")
    print("  • The Nexus server must be running on the remote host")

    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    target_name = utils.get_user_input("Target name (for reference)", required=True)
    if target_name == "local":
        print(colored("'local' is reserved for localhost connections", "red"))
        return

    if target_name in cfg.targets:
        print(colored(f"Target '{target_name}' already exists", "yellow"))
        if not utils.ask_yes_no("Overwrite existing target?"):
            return

    host = utils.get_user_input("Remote server address (hostname or IP)", required=True)
    port = int(utils.get_user_input("Remote server port", default="54323"))
    ssh_user = utils.get_user_input("SSH username", default=os.environ.get("USER", ""))

    print(colored("\nValidating connection...", "cyan", attrs=["bold"]))

    if not _test_ssh_connection(host, ssh_user):
        print(colored("\n✗ SSH connection failed", "red"))
        print(colored("\nTo fix:", "yellow"))
        print(colored(f"  1. Ensure your SSH key is in ~/.ssh/authorized_keys on {host}", "yellow"))
        print(colored(f"  2. Test manually: ssh {ssh_user}@{host} echo ok", "yellow"))
        return

    target_cfg = config.TargetConfig(host=host, port=port, ssh_user=ssh_user)
    cfg.targets[target_name] = target_cfg
    config.save_config(cfg)

    success, server_name = _test_tunnel_and_server(target_name)
    if not success:
        del cfg.targets[target_name]
        config.save_config(cfg)
        print(colored("\n✗ Failed to connect to Nexus server via SSH tunnel", "red"))
        return

    if not cfg.default_target:
        cfg.default_target = target_name
        config.save_config(cfg)

    print(colored(f"\n✓ Target '{target_name}' configured", "green", attrs=["bold"]))
    print(f"Server node: {server_name}")
    print(f"Configuration saved to: {config.get_config_path()}")


def list_targets() -> None:
    cfg = config.load_config()
    default = cfg.default_target or "local"

    print(colored("Targets:", "blue", attrs=["bold"]))
    print(f"{'* ' if default == 'local' else '  '}local (http://127.0.0.1:54323)")

    for name, target in cfg.targets.items():
        marker = "* " if name == default else "  "
        print(f"{marker}{name} ({target.ssh_user}@{target.host}:{target.port})")


def set_default_target(target_name: str) -> None:
    cfg = config.load_config()

    if target_name != "local" and target_name not in cfg.targets:
        print(colored(f"Target '{target_name}' not found", "red"))
        print(colored("Use 'nx targets list' to see available targets", "yellow"))
        return

    cfg = tp.cast(config.NexusCliConfig, cfg.copy(update={"default_target": target_name}))
    config.save_config(cfg)
    print(colored(f"✓ Default target: {target_name}", "green"))


def remove_target(target_name: str) -> None:
    cfg = config.load_config()

    if target_name not in cfg.targets:
        print(colored(f"Target '{target_name}' not found", "red"))
        return

    if not utils.ask_yes_no(f"Remove target '{target_name}'?"):
        print(colored("Operation cancelled.", "yellow"))
        return

    del cfg.targets[target_name]

    if cfg.default_target == target_name:
        cfg.default_target = None

    config.save_config(cfg)
    print(colored(f"✓ Removed target '{target_name}'", "green"))


def check_config_exists() -> bool:
    config_path = config.get_config_path()
    return config_path.exists() and config_path.stat().st_size > 0
