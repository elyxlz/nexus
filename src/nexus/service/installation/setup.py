"""
Nexus Service Installation Module

System Mode: Installs as a systemd service (requires sudo)
User Mode: Runs in user's session (no sudo required)

Configuration can be set using environment variables with NS_ prefix.
"""

import argparse
import getpass
import importlib.metadata
import importlib.util
import json
import os
import pathlib as pl
import pwd
import shutil
import subprocess
import sys
import typing as tp
from dataclasses import dataclass

from nexus.service.core import config, context, db, logger

# Installation paths
SYSTEM_CONFIG_DIR = pl.Path("/etc/nexus_service")
USER_CONFIG_DIR = pl.Path.home() / ".nexus_service"
SYSTEMD_DIR = pl.Path("/etc/systemd/system")
SERVICE_FILENAME = "nexus.service"
SERVICE_USER = "nexus"

# Marker files
MARKER_SYSTEM = SYSTEM_CONFIG_DIR / "nexus_service.json"
MARKER_USER = USER_CONFIG_DIR / "nexus_service.json"


@dataclass(frozen=True)
class InstallationInfo:
    version: str
    install_date: str
    install_mode: tp.Literal["system", "user", "none"] = "none"
    install_path: pl.Path | None = None
    config_path: pl.Path | None = None
    installed_by: str | None = None
    service_enabled: bool = False


def get_installation_info() -> InstallationInfo:
    if MARKER_SYSTEM.exists():
        try:
            data = json.loads(MARKER_SYSTEM.read_text())
            return InstallationInfo(
                version=data.get("version", "unknown"),
                install_date=data.get("install_date", "unknown"),
                install_mode="system",
                install_path=SYSTEM_CONFIG_DIR,
                config_path=SYSTEM_CONFIG_DIR / "config.toml",
                installed_by=data.get("installed_by"),
                service_enabled=data.get("service_enabled", False),
            )
        except Exception:
            pass

    if MARKER_USER.exists():
        try:
            data = json.loads(MARKER_USER.read_text())
            return InstallationInfo(
                version=data.get("version", "unknown"),
                install_date=data.get("install_date", "unknown"),
                install_mode="user",
                install_path=USER_CONFIG_DIR,
                config_path=USER_CONFIG_DIR / "config.toml",
                installed_by=data.get("installed_by"),
                service_enabled=False,
            )
        except Exception:
            pass

    try:
        current_version = importlib.metadata.version("nexusai")
    except importlib.metadata.PackageNotFoundError:
        current_version = "unknown"

    return InstallationInfo(
        version=current_version,
        install_date="",
        install_mode="none",
        install_path=None,
        config_path=None,
        installed_by=None,
    )


def get_service_directory() -> tuple[pl.Path | None, bool]:
    info = get_installation_info()

    if info.install_mode == "system":
        return SYSTEM_CONFIG_DIR, False
    elif info.install_mode == "user":
        return USER_CONFIG_DIR, False

    # Check for user config without marker
    if (USER_CONFIG_DIR / "config.toml").exists():
        return USER_CONFIG_DIR, False

    return None, True


def require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("This operation requires root privileges. Please run with sudo.")


def verify_external_dependencies() -> None:
    missing = []
    for cmd in ["git", "screen"]:
        if shutil.which(cmd) is None:
            missing.append(cmd)

    if missing:
        sys.exit(f"Missing dependencies: {missing}")


def handle_version_check() -> None:
    try:
        current_version = importlib.metadata.version("nexusai")
        import requests

        r = requests.get("https://pypi.org/pypi/nexusai/json", timeout=2)
        data = r.json()
        remote_version = data["info"]["version"]
        if remote_version > current_version:
            print(f"New version available: {remote_version} (current: {current_version})")
    except Exception:
        pass


def write_installation_marker(mode: tp.Literal["system", "user"], service_enabled: bool = False) -> str:
    from datetime import datetime

    try:
        current_version = importlib.metadata.version("nexusai")
    except importlib.metadata.PackageNotFoundError:
        current_version = "unknown"

    marker_path = MARKER_SYSTEM if mode == "system" else MARKER_USER
    install_data = {
        "version": current_version,
        "install_date": datetime.now().isoformat(),
        "install_mode": mode,
        "installed_by": getpass.getuser(),
        "service_enabled": service_enabled,
    }

    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(install_data, indent=2))
    os.chmod(marker_path, 0o644)

    return current_version


def create_directories(config_dir: pl.Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "logs").mkdir(parents=True, exist_ok=True)
    (config_dir / "jobs").mkdir(parents=True, exist_ok=True)


def create_service_user() -> bool:
    try:
        pwd.getpwnam(SERVICE_USER)
        return False
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", SERVICE_USER], check=True)
        return True


def configure_multiuser_screen() -> bool:
    screenrc_path = pl.Path(f"/home/{SERVICE_USER}/.screenrc")
    if not screenrc_path.exists():
        screenrc_path.write_text("multiuser on\nacladd .\n")
        subprocess.run(["chown", f"{SERVICE_USER}:{SERVICE_USER}", str(screenrc_path)], check=True)
        return True
    return False


def set_system_permissions() -> None:
    subprocess.run(["chown", "-R", f"{SERVICE_USER}:{SERVICE_USER}", str(SYSTEM_CONFIG_DIR)], check=True)
    subprocess.run(["chmod", "-R", "770", str(SYSTEM_CONFIG_DIR)], check=True)


def setup_systemd_service() -> tuple[bool, str | None]:
    from nexus.service.installation import systemd

    service_content = systemd.get_service_file_content()
    dest_service = SYSTEMD_DIR / SERVICE_FILENAME
    dest_service.write_text(service_content)
    return True, None


def manage_systemd_service(action: str) -> bool:
    try:
        if action == "start":
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", SERVICE_FILENAME], check=True)
            subprocess.run(["systemctl", "start", SERVICE_FILENAME], check=True)
            return True
        elif action == "stop":
            subprocess.run(["systemctl", "stop", SERVICE_FILENAME], check=False)
            subprocess.run(["systemctl", "disable", SERVICE_FILENAME], check=False)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            return True
        return False
    except subprocess.CalledProcessError:
        return False


def install_system_service() -> None:
    service_ok, service_error = setup_systemd_service()
    if not service_ok:
        sys.exit(service_error)
    print(f"Installed service file to: {SYSTEMD_DIR / SERVICE_FILENAME}")


def start_system_service() -> bool:
    service_started = manage_systemd_service("start")
    print("Nexus service " + ("enabled and started." if service_started else "failed to start."))
    return service_started


def stop_system_service() -> bool:
    service_stopped = manage_systemd_service("stop")
    print("Nexus service " + ("disabled and stopped." if service_stopped else "failed to stop."))
    return service_stopped


def create_interactive_config(default_config: config.NexusServiceConfig) -> config.NexusServiceConfig:
    print("\nNexus Service Configuration")
    print("=========================")
    print("(You can also set these values with environment variables: NS_HOST, NS_PORT, etc.)")

    host = input(f"Host [default: {default_config.host}]: ").strip() or default_config.host

    port_str = input(f"Port [default: {default_config.port}]: ").strip()
    port = int(port_str) if port_str.isdigit() else default_config.port

    node_name = input(f"Node name [default: {default_config.node_name}]: ").strip() or default_config.node_name

    log_level = (
        input(f"Log level (debug/info/warning/error) [default: {default_config.log_level}]: ").strip()
        or default_config.log_level
    )

    return config.NexusServiceConfig(
        service_dir=default_config.service_dir,
        host=host,
        port=port,
        node_name=node_name,
        log_level=log_level,
    )


def setup_config(
    config_dir: pl.Path, interactive: bool = True, config_file: pl.Path | None = None
) -> config.NexusServiceConfig:
    default_config = config.NexusServiceConfig(service_dir=config_dir)

    if config_file and config_file.exists():
        try:
            # Load config from service directory
            file_config = config.load_config(config_dir)
            file_config.service_dir = config_dir  # Ensure correct service_dir
            return file_config
        except Exception as e:
            print(f"Error loading config file: {e}")
            print("Falling back to default configuration.")

    if not interactive:
        return default_config

    return create_interactive_config(default_config)


def display_config(_config: config.NexusServiceConfig) -> None:
    print("\nCurrent Configuration")
    print("======================")
    for key, value in _config.model_dump().items():
        print(f"{key}: {value}")
    print()


def edit_config(config_path: pl.Path) -> bool:
    if not config_path.exists():
        print(f"Configuration file not found: {config_path}")
        return False

    editor = os.environ.get("EDITOR", "")
    if not editor:
        if os.path.exists("/usr/bin/nano"):
            editor = "nano"
        else:
            editor = "vi"

    try:
        result = subprocess.run([editor, str(config_path)])
        return result.returncode == 0
    except Exception as e:
        print(f"Error editing configuration: {e}")
        return False


def create_persistent_directory(_config: config.NexusServiceConfig) -> None:
    if _config.service_dir is None:
        raise ValueError("Service directory cannot be None")

    _config.service_dir.mkdir(parents=True, exist_ok=True)
    config.get_jobs_dir(_config.service_dir).mkdir(parents=True, exist_ok=True)
    config.get_log_dir(_config.service_dir).mkdir(parents=True, exist_ok=True)

    # Create the configuration file
    config.save_config(_config)


def remove_installation_files(mode: tp.Literal["system", "user"], keep_config: bool) -> None:
    config_dir = SYSTEM_CONFIG_DIR if mode == "system" else USER_CONFIG_DIR
    if not keep_config and config_dir.exists():
        shutil.rmtree(config_dir, ignore_errors=True)
        print(f"Removed directory: {config_dir}")
    else:
        marker = MARKER_SYSTEM if mode == "system" else MARKER_USER
        if marker.exists():
            marker.unlink()
        if keep_config:
            print(f"Kept configuration directory: {config_dir}")


def remove_service_user() -> bool:
    try:
        pwd.getpwnam(SERVICE_USER)
        subprocess.run(["userdel", "-r", SERVICE_USER], check=True)
        return True
    except (KeyError, subprocess.CalledProcessError):
        return False


def remove_service_files() -> None:
    service_file = SYSTEMD_DIR / SERVICE_FILENAME
    if service_file.exists():
        service_file.unlink()


def remove_system_components() -> None:
    stop_system_service()
    remove_service_files()
    print(f"Removed service file: {SYSTEMD_DIR / SERVICE_FILENAME}")


def check_installation_prerequisites(mode: tp.Literal["system", "user"], force: bool = False) -> None:
    if importlib.util.find_spec("nexus") is None:
        sys.exit(
            "ERROR: The 'nexus' package is not available in the system Python environment.\n"
            "Install it with: sudo pip3 install nexusai"
        )

    info = get_installation_info()
    if info.install_mode != "none" and not force:
        if info.install_mode == mode:
            sys.exit(f"Nexus service is already installed in {mode} mode (version {info.version}).")
        else:
            other_mode = "user" if mode == "system" else "system"
            sys.exit(
                f"Nexus service is already installed in {other_mode} mode (version {info.version}).\n"
                f"Please uninstall the {other_mode} installation first or use --force."
            )


def prepare_system_environment() -> None:
    create_directories(SYSTEM_CONFIG_DIR)
    print(f"Created system directory: {SYSTEM_CONFIG_DIR}")

    if create_service_user():
        print(f"Created {SERVICE_USER} system user.")
    else:
        print(f"User '{SERVICE_USER}' already exists.")

    if configure_multiuser_screen():
        print(f"Configured multiuser Screen for {SERVICE_USER} user.")


def print_installation_complete_message(mode: tp.Literal["system", "user"]) -> None:
    print(f"\n{mode.capitalize()} installation complete.")
    if mode == "system":
        print("To uninstall: nexus-service uninstall")
        print("To start/stop: sudo systemctl start/stop nexus_service")
        print("To check status: systemctl status nexus_service")
    else:
        print("To uninstall: nexus-service uninstall")
        print("To start the service: nexus-service")
        print("To check status: nexus-service status")


def install_system(
    interactive: bool = True, config_file: pl.Path | None = None, start_service: bool = True, force: bool = False
) -> None:
    require_root()
    verify_external_dependencies()
    check_installation_prerequisites("system", force)

    print("Installing Nexus service in system mode...")
    prepare_system_environment()

    _config = setup_config(SYSTEM_CONFIG_DIR, interactive, config_file)
    create_persistent_directory(_config)
    print(f"Created configuration at: {config.get_config_path(SYSTEM_CONFIG_DIR)}")

    if interactive:
        display_config(_config)

    set_system_permissions()
    print("Set proper directory permissions.")

    install_system_service()
    service_started = False
    if start_service:
        service_started = start_system_service()

    current_version = write_installation_marker("system", service_started)
    print(f"Installed version {current_version}")

    print_installation_complete_message("system")


def install_user(interactive: bool = True, config_file: pl.Path | None = None, force: bool = False) -> None:
    verify_external_dependencies()
    check_installation_prerequisites("user", force)

    print("Installing Nexus service in user mode...")
    create_directories(USER_CONFIG_DIR)
    print(f"Created user directory: {USER_CONFIG_DIR}")

    _config = setup_config(USER_CONFIG_DIR, interactive, config_file)
    create_persistent_directory(_config)
    print(f"Created configuration at: {config.get_config_path(USER_CONFIG_DIR)}")

    if interactive:
        display_config(_config)

    current_version = write_installation_marker("user")
    print(f"Installed version {current_version}")

    print_installation_complete_message("user")


def uninstall(keep_config: bool = False, force: bool = False) -> None:
    info = get_installation_info()

    if info.install_mode == "none" and not force:
        print("Nexus service is not installed.")
        return

    if info.install_mode == "system":
        require_root()
        print("Uninstalling system installation...")
        remove_system_components()
        remove_installation_files("system", keep_config)

        if remove_service_user():
            print(f"Removed {SERVICE_USER} system user.")

    elif info.install_mode == "user":
        print("Uninstalling user installation...")
        remove_installation_files("user", keep_config)

    print("\nNexus service has been uninstalled.")


def command_status() -> None:
    info = get_installation_info()

    print("\nNexus Service Status")
    print("===================")

    if info.install_mode == "none":
        print("Installation: Not installed")
    else:
        print(f"Installation: {info.install_mode.capitalize()} mode")
        print(f"Version: {info.version}")
        print(f"Installed on: {info.install_date}")
        if info.installed_by:
            print(f"Installed by: {info.installed_by}")
        print(f"Service directory: {info.install_path}")

    if info.install_mode == "system":
        try:
            result = subprocess.run(["systemctl", "is-active", "nexus.service"], capture_output=True, text=True)
            is_active = result.stdout.strip() == "active"

            result = subprocess.run(["systemctl", "is-enabled", "nexus.service"], capture_output=True, text=True)
            is_enabled = result.stdout.strip() == "enabled"

            print(f"Service active: {'Yes' if is_active else 'No'}")
            print(f"Service enabled: {'Yes' if is_enabled else 'No'}")
        except Exception as e:
            print(f"Error checking service status: {e}")


def command_config(edit_mode: bool = False) -> None:
    info = get_installation_info()

    if info.install_mode == "none":
        print("Nexus service is not installed. Using default configuration.")
        config_obj = config.NexusServiceConfig(service_dir=None)
        if edit_mode:
            print("Cannot edit configuration without an installation.")
            return
    else:
        if not info.config_path or not info.config_path.exists():
            print(f"Configuration file not found at expected location: {info.config_path}")
            return

        if edit_mode:
            if edit_config(info.config_path):
                print(f"Configuration edited successfully: {info.config_path}")
                # Reload to show updated config
                if info.install_path is not None:
                    config_obj = config.load_config(info.install_path)
                else:
                    print("Cannot load configuration - install path is None")
                    return
            else:
                print("Configuration editing canceled or failed.")
                return
        else:
            if info.install_path is not None:
                config_obj = config.load_config(info.install_path)
            else:
                print("Cannot load configuration - install path is None")
                return

    display_config(config_obj)


def handle_install_command(args: argparse.Namespace) -> None:
    interactive = not getattr(args, "no_interactive", False)
    force = getattr(args, "force", False)
    config_file = pl.Path(args.config) if args.config else None

    if getattr(args, "user", False):
        install_user(interactive=interactive, config_file=config_file, force=force)
    else:
        start_service = not getattr(args, "no_start", False)
        install_system(interactive=interactive, config_file=config_file, start_service=start_service, force=force)


def handle_uninstall_command(args: argparse.Namespace) -> None:
    uninstall(keep_config=args.keep_config, force=args.force)


def handle_config_command(args: argparse.Namespace) -> None:
    command_config(edit_mode=args.edit)


def handle_command(args: argparse.Namespace) -> bool:
    command_handlers = {
        "install": handle_install_command,
        "uninstall": handle_uninstall_command,
        "config": handle_config_command,
        "status": lambda _: command_status(),
    }

    if args.command in command_handlers:
        command_handlers[args.command](args)
        return True

    return False


def prompt_installation_mode() -> None:
    print("First run detected. Nexus service is not installed.")
    print("You can run in the following modes:")
    print("  1. Install as system service (requires sudo)")
    print("  2. Install for current user only")
    print("  3. Run without installing (stateless)")

    try:
        choice = input("Select mode [1-3, default=1]: ").strip()

        if choice == "2":
            install_user(interactive=True)
            sys.exit(0)
        elif choice == "3":
            print("Running in stateless mode...")
            return

    except KeyboardInterrupt:
        print("\nSetup cancelled")
        sys.exit(0)

    print("\nSetup cancelled")
    sys.exit(0)


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nexus Service: GPU Job Management Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nexus-service                      # Run the service
  nexus-service install              # Install as system service
  nexus-service install --user       # Install for current user only
  nexus-service uninstall            # Remove installation
  nexus-service config               # Show current configuration
  nexus-service config --edit        # Edit configuration in text editor
  nexus-service status               # Check service status

Configuration can also be set using environment variables (prefix=NS_):
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    install_parser = subparsers.add_parser("install", help="Install Nexus service")
    install_parser.add_argument(
        "--user", action="store_true", help="Install for current user only (default: system installation)"
    )
    install_parser.add_argument("--config", help="Path to config file for non-interactive setup")
    install_parser.add_argument("--no-interactive", action="store_true", help="Skip interactive configuration")
    install_parser.add_argument("--force", action="store_true", help="Force installation even if already installed")
    install_parser.add_argument("--no-start", action="store_true", help="Don't start service after installation")

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall Nexus service")
    uninstall_parser.add_argument(
        "--keep-config", action="store_true", help="Keep configuration files when uninstalling"
    )
    uninstall_parser.add_argument("--force", action="store_true", help="Force uninstallation even if not installed")

    config_parser = subparsers.add_parser("config", help="Manage Nexus service configuration")
    config_parser.add_argument("--edit", action="store_true", help="Edit configuration in text editor")

    subparsers.add_parser("status", help="Show Nexus service status")

    return parser


def initialize_service(service_dir: pl.Path | None) -> context.NexusServiceContext:
    if service_dir and (service_dir / "config.toml").exists():
        _config = config.load_config(service_dir)
    else:
        _config = config.NexusServiceConfig(service_dir=service_dir)

    db_path = ":memory:" if _config.service_dir is None else str(config.get_db_path(_config.service_dir))
    log_dir = None if _config.service_dir is None else config.get_log_dir(_config.service_dir)

    if _config.service_dir is not None:
        create_persistent_directory(_config)

    _logger = logger.create_service_logger(log_dir, name="nexus_service", log_level=_config.log_level)
    _db = db.create_connection(_logger, db_path=db_path)

    return context.NexusServiceContext(db=_db, config=_config, logger=_logger)
