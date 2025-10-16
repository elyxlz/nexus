import os
import pathlib as pl
import typing as tp

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


def _download_server_certificate(host: str, port: int) -> pl.Path:
    import ssl

    print(colored("\nDownloading server certificate...", "cyan"))

    try:
        cert_pem = ssl.get_server_certificate((host, port), timeout=10)
        cert_path = config.get_server_cert_path(host, port)
        cert_path.parent.mkdir(exist_ok=True)
        cert_path.write_text(cert_pem)
        print(colored("Certificate saved", "green"))
        return cert_path
    except TimeoutError:
        print(colored(f"\n✗ Connection to {host}:{port} timed out", "red"))
        print(colored("\nPossible causes:", "yellow"))
        print(colored("  • Server is not running", "yellow"))
        print(colored("  • Firewall is blocking port access", "yellow"))
        print(colored("  • Network connectivity issues", "yellow"))
        print(colored("\nTo fix firewall issues on the server, run:", "cyan"))
        print(colored(f"  sudo ufw allow {port}/tcp", "white"))
        print(colored("  or", "white"))
        print(colored(f"  sudo firewall-cmd --permanent --add-port={port}/tcp", "white"))
        print(colored("  sudo firewall-cmd --reload", "white"))
        raise
    except ConnectionRefusedError:
        print(colored(f"\n✗ Connection to {host}:{port} refused", "red"))
        print(colored("\nPossible causes:", "yellow"))
        print(colored("  • Server is not running on this port", "yellow"))
        print(colored("  • Server is not listening on external interfaces", "yellow"))
        print(colored("\nVerify the server is running:", "cyan"))
        print(colored("  sudo systemctl status nexus-server", "white"))
        raise
    except ssl.SSLError as e:
        print(colored(f"\n✗ SSL error: {e}", "red"))
        print(colored("\nThe server may not have SSL enabled yet.", "yellow"))
        raise
    except Exception as e:
        print(colored(f"\n✗ Failed to download certificate: {e}", "red"))
        raise


def _generate_ssh_key(host: str, port: int) -> pl.Path:
    import subprocess

    ssh_key_path = config.get_ssh_key_path(host, port)
    if not ssh_key_path.exists():
        print(colored(f"\nGenerating SSH key at {ssh_key_path}...", "cyan"))
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(ssh_key_path), "-N", "", "-C", "nexus-client"],
            check=True,
            capture_output=True,
        )
    return ssh_key_path


def _register_ssh_key_with_server(public_key: str, target_cfg: config.TargetConfig) -> bool:
    from nexus.cli import api_client

    print(colored("\nRegistering SSH key with remote server...", "cyan"))
    try:
        result = api_client.register_ssh_key(public_key, target_cfg=target_cfg)
        if result.get("status") == "exists":
            print(colored("SSH key already registered", "yellow"))
        else:
            print(colored("SSH key registered successfully", "green"))
        return True
    except Exception as e:
        print(colored(f"Failed to register SSH key: {e}", "red"))
        print(colored("\nYou may need to manually add this key to the server:", "yellow"))
        print(public_key)
        return False


def add_target() -> None:
    import requests

    print(colored("\nAdd Target Server", "blue", attrs=["bold"]))
    print("Configure CLI to connect to a remote Nexus server.")

    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    host = utils.get_user_input("Remote server address", required=True)
    port = int(utils.get_user_input("Remote server port", default="54323"))
    api_token = utils.get_user_input("API token", required=True, mask_input=True)
    protocol = "https"

    print(colored("\n🔍 Connecting to server...", "cyan"))

    cert_path = None
    try:
        cert_path = _download_server_certificate(host, port)
    except Exception:
        return

    try:
        url = f"{protocol}://{host}:{port}/v1/server/status"
        headers = {"Authorization": f"Bearer {api_token}"}

        response = requests.get(url, headers=headers, verify=False, timeout=10)
        response.raise_for_status()
        status = response.json()

        server_name = status.get("node_name")
        if not server_name:
            raise ValueError("Server did not return a node_name")

        print(colored(f"✓ Connected to: {server_name}", "green"))

    except Exception as e:
        print(colored(f"Failed to connect to server: {e}", "red"))
        if cert_path and cert_path.exists():
            cert_path.unlink()
        return

    if server_name in cfg.targets:
        print(colored(f"Target '{server_name}' already exists", "yellow"))
        if not utils.ask_yes_no("Overwrite existing target?"):
            return

    ssh_key_path = _generate_ssh_key(host, port)
    pub_key_path = pl.Path(str(ssh_key_path) + ".pub")
    public_key = pub_key_path.read_text().strip()

    target_cfg = config.TargetConfig(host=host, port=port, protocol=protocol, api_token=api_token)

    success = _register_ssh_key_with_server(public_key, target_cfg)
    if not success:
        print(colored("\n✗ Failed to register SSH key with server", "red"))
        print(colored("Target not saved. Please resolve the issue and try again.", "yellow"))
        return

    cfg.targets[server_name] = target_cfg

    if not cfg.default_target:
        cfg.default_target = server_name

    config.save_config(cfg)

    print(colored(f"\n✓ Target '{server_name}' configured", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")


def list_targets() -> None:
    cfg = config.load_config()
    default = cfg.default_target or "local"

    print(colored("Targets:", "blue", attrs=["bold"]))
    print(f"{'* ' if default == 'local' else '  '}local (http://localhost:54323)")

    for name, target in cfg.targets.items():
        marker = "* " if name == default else "  "
        print(f"{marker}{name} ({target.protocol}://{target.host}:{target.port})")


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

    target = cfg.targets[target_name]
    ssh_key_path = config.get_ssh_key_path(target.host, target.port)
    pub_key_path = pl.Path(str(ssh_key_path) + ".pub")
    cert_path = config.get_server_cert_path(target.host, target.port)

    deleted_files = []
    if ssh_key_path.exists():
        ssh_key_path.unlink()
        deleted_files.append(str(ssh_key_path))
    if pub_key_path.exists():
        pub_key_path.unlink()
        deleted_files.append(str(pub_key_path))
    if cert_path.exists():
        cert_path.unlink()
        deleted_files.append(str(cert_path))

    del cfg.targets[target_name]

    if cfg.default_target == target_name:
        cfg.default_target = None

    config.save_config(cfg)
    print(colored(f"✓ Removed target '{target_name}'", "green"))
    if deleted_files:
        print(colored(f"Deleted {len(deleted_files)} associated file(s)", "cyan"))


def check_config_exists() -> bool:
    config_path = config.get_config_path()
    return config_path.exists() and config_path.stat().st_size > 0
