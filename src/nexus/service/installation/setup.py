"""
Nexus Service Installation Module

This module provides functions for installing, configuring, and managing the
Nexus service in different deployment modes:

1. System Mode: Installs as a systemd service with dedicated user (requires sudo)
2. User Mode: Runs directly in user's session without systemd (no sudo required)

The installation process handles:
- Directory creation and permissions
- Configuration setup (interactive or non-interactive)
- Service user creation (for system mode)
- Systemd service setup (for system mode)
- Screen multi-user configuration
- Version tracking and updates

Usage examples:
- nexus-service install --system    # Install as system service (default)
- nexus-service install --user      # Install for current user only
- nexus-service uninstall           # Remove installation
- nexus-service config              # Configure service
- nexus-service status              # Check service status
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

from nexus.service.core import config, context, db, env, logger

# =========================================================================
# Constants and Types
# =========================================================================

# Installation paths
SYSTEM_CONFIG_DIR = pl.Path("/etc/nexus_service")
USER_CONFIG_DIR = pl.Path.home() / ".nexus_service"
SYSTEMD_DIR = pl.Path("/etc/systemd/system")
SERVICE_FILENAME = "nexus.service"
SERVICE_USER = "nexus"
SCRIPT_DIR = pl.Path(__file__).parent

# Marker files
MARKER_SYSTEM = SYSTEM_CONFIG_DIR / "nexus_service.json"
MARKER_USER = USER_CONFIG_DIR / "nexus_service.json"


@dataclass(frozen=True)
class InstallationInfo:
    """Information about the current Nexus service installation."""

    version: str
    install_date: str
    install_mode: tp.Literal["system", "user", "none"] = "none"
    install_path: pl.Path | None = None
    config_path: pl.Path | None = None
    installed_by: str | None = None
    service_enabled: bool = False


# =========================================================================
# Installation Status Functions
# =========================================================================


def get_installation_info() -> InstallationInfo:
    """Get information about the current installation state."""
    # Check for system installation first
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

    # Check for user installation
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

    # No installation found
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


def require_root() -> None:
    """Exit if not running as root."""
    if os.geteuid() != 0:
        sys.exit("This operation requires root privileges. Please run with sudo.")


# =========================================================================
# Version and Dependency Management
# =========================================================================


def fetch_latest_version() -> tuple[bool, str, str | None]:
    try:
        import requests

        r = requests.get("https://pypi.org/pypi/nexusai/json", timeout=2)
        r.raise_for_status()
        data = r.json()
        return True, data["info"]["version"], None
    except Exception as e:
        return False, "", str(e)


def handle_version_check() -> None:
    try:
        current_version = importlib.metadata.version("nexusai")
        success, remote_version, _ = fetch_latest_version()
        if success and remote_version > current_version:
            print(f"New version available: {remote_version} (current: {current_version})")
    except Exception:
        pass


def verify_external_dependencies() -> None:
    missing = []
    for cmd in ["git", "screen"]:
        if shutil.which(cmd) is None:
            missing.append(cmd)

    if missing:
        sys.exit(f"Missing dependencies: {missing}")


# =========================================================================
# Marker File Management
# =========================================================================


def write_installation_marker(mode: tp.Literal["system", "user"], service_enabled: bool = False) -> str:
    """Write installation marker file with metadata."""
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

    # Ensure directory exists
    marker_path.parent.mkdir(parents=True, exist_ok=True)

    # Write marker file
    marker_path.write_text(json.dumps(install_data, indent=2))
    os.chmod(marker_path, 0o644)

    return current_version


# =========================================================================
# Directory and User Management
# =========================================================================


def create_directories(config_dir: pl.Path) -> None:
    """Create necessary directories for nexus service."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "logs").mkdir(parents=True, exist_ok=True)
    (config_dir / "jobs").mkdir(parents=True, exist_ok=True)


def create_service_user() -> bool:
    """Create system user for the service."""
    try:
        pwd.getpwnam(SERVICE_USER)
        return False
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", SERVICE_USER], check=True)
        return True


def configure_multiuser_screen() -> bool:
    """Configure multi-user screen for service user."""
    screenrc_path = pl.Path(f"/home/{SERVICE_USER}/.screenrc")
    if not screenrc_path.exists():
        screenrc_path.write_text("multiuser on\nacladd .\n")
        subprocess.run(["chown", f"{SERVICE_USER}:{SERVICE_USER}", str(screenrc_path)], check=True)
        return True
    return False


def set_system_permissions() -> None:
    """Set correct permissions for system directories."""
    subprocess.run(["chown", "-R", f"{SERVICE_USER}:{SERVICE_USER}", str(SYSTEM_CONFIG_DIR)], check=True)
    subprocess.run(["chmod", "-R", "770", str(SYSTEM_CONFIG_DIR)], check=True)


# =========================================================================
# Systemd Management
# =========================================================================


def setup_systemd_service() -> tuple[bool, str | None]:
    """Install systemd service file."""
    from nexus.service.installation import nexus_service

    # Get service content from the module
    service_content = nexus_service.get_service_file_content()

    # Write service file
    dest_service = SYSTEMD_DIR / SERVICE_FILENAME
    dest_service.write_text(service_content)

    return True, None


def manage_systemd_service(action: str) -> bool:
    """Manage systemd service (start, stop, enable, disable)."""
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


# =========================================================================
# Configuration Management
# =========================================================================


def setup_config(
    config_dir: pl.Path, interactive: bool = True, config_file: pl.Path | None = None
) -> config.NexusServiceConfig:
    """Set up configuration interactively or from a file."""
    # Create a default configuration
    default_config = config.NexusServiceConfig(service_dir=config_dir)

    # If a config file is provided, use it
    if config_file and config_file.exists():
        import toml

        try:
            config_data = toml.load(config_file)
            # Override service_dir to match installation dir
            config_data["service_dir"] = str(config_dir)
            return config.NexusServiceConfig(**config_data)
        except Exception as e:
            print(f"Error loading config file: {e}")
            print("Falling back to default configuration.")

    # If not interactive, just use defaults
    if not interactive:
        return default_config

    # Interactive configuration
    print("\nNexus Service Configuration")
    print("=========================")

    host = input(f"Host [default: {default_config.host}]: ").strip() or default_config.host

    port_str = input(f"Port [default: {default_config.port}]: ").strip()
    port = int(port_str) if port_str.isdigit() else default_config.port

    node_name = input(f"Node name [default: {default_config.node_name}]: ").strip() or default_config.node_name

    webhooks_enabled_str = (
        input(f"Enable webhooks (y/n) [default: {'y' if default_config.webhooks_enabled else 'n'}]: ").strip().lower()
    )
    webhooks_enabled = (
        webhooks_enabled_str == "y" if webhooks_enabled_str in ("y", "n") else default_config.webhooks_enabled
    )

    webhook_url = ""
    if webhooks_enabled:
        webhook_url = (
            input(f"Webhook URL [default: {default_config.webhook_url or 'none'}]: ").strip()
            or default_config.webhook_url
        )

    log_level = (
        input(f"Log level (debug/info/warning/error) [default: {default_config.log_level}]: ").strip()
        or default_config.log_level
    )

    # Create config with user values
    return config.NexusServiceConfig(
        service_dir=config_dir,
        host=host,
        port=port,
        node_name=node_name,
        webhooks_enabled=webhooks_enabled,
        webhook_url=webhook_url,
        log_level=log_level,
    )


def create_persistent_directory(_config: config.NexusServiceConfig, _env: env.NexusServiceEnv) -> None:
    """Create persistent directories and initial config files."""
    if _config.service_dir is None:
        raise ValueError("Service directory cannot be None")

    # Ensure service directory exists
    _config.service_dir.mkdir(parents=True, exist_ok=True)

    # Create the environment file if it doesn't exist
    if not config.get_env_path(_config.service_dir).exists():
        env.save_env(_env, env_path=config.get_env_path(_config.service_dir))

    # Ensure the jobs directory exists
    config.get_jobs_dir(_config.service_dir).mkdir(parents=True, exist_ok=True)

    # Ensure the logs directory exists
    config.get_log_dir(_config.service_dir).mkdir(parents=True, exist_ok=True)

    # Create the configuration file
    config.save_config(_config)


# =========================================================================
# Uninstallation Functions
# =========================================================================


def cleanup_files(mode: tp.Literal["system", "user"], keep_config: bool = False) -> None:
    """Clean up installation files."""
    config_dir = SYSTEM_CONFIG_DIR if mode == "system" else USER_CONFIG_DIR
    marker = MARKER_SYSTEM if mode == "system" else MARKER_USER

    if not keep_config and config_dir.exists():
        shutil.rmtree(config_dir, ignore_errors=True)
    elif marker.exists():
        marker.unlink()


def remove_service_user() -> bool:
    """Remove service user."""
    try:
        pwd.getpwnam(SERVICE_USER)
        subprocess.run(["userdel", "-r", SERVICE_USER], check=True)
        return True
    except (KeyError, subprocess.CalledProcessError):
        return False


def remove_service_files() -> None:
    """Remove systemd service files."""
    service_file = SYSTEMD_DIR / SERVICE_FILENAME
    if service_file.exists():
        service_file.unlink()


# =========================================================================
# Main Installation Functions
# =========================================================================


def install_system(
    interactive: bool = True, config_file: pl.Path | None = None, start_service: bool = True, force: bool = False
) -> None:
    """Install Nexus service in system mode."""
    require_root()

    # Verify the nexus package is available
    if importlib.util.find_spec("nexus") is None:
        sys.exit(
            "ERROR: The 'nexus' package is not available in the system Python environment.\n"
            "Install it with: sudo pip3 install nexusai"
        )

    # Check for existing installation
    info = get_installation_info()
    if info.install_mode != "none" and not force:
        if info.install_mode == "system":
            print(f"Nexus service is already installed in system mode (version {info.version}).")
            return
        else:
            print(f"Nexus service is already installed in user mode (version {info.version}).")
            print("Please uninstall the user installation first or use --force.")
            return

    # Check dependencies
    deps_ok, deps_error = verify_external_dependencies()
    if not deps_ok:
        sys.exit(f"Missing dependencies: {deps_error}")

    print("Installing Nexus service in system mode...")

    # Create system directories
    create_directories(SYSTEM_CONFIG_DIR)
    print(f"Created system directory: {SYSTEM_CONFIG_DIR}")

    # Create service user
    if create_service_user():
        print(f"Created {SERVICE_USER} system user.")
    else:
        print(f"User '{SERVICE_USER}' already exists.")

    # Configure screen
    if configure_multiuser_screen():
        print(f"Configured multiuser Screen for {SERVICE_USER} user.")

    # Set up configuration
    _config = setup_config(SYSTEM_CONFIG_DIR, interactive, config_file)
    _env = env.NexusServiceEnv()
    create_persistent_directory(_config, _env)
    print(f"Created configuration at: {config.get_config_path(SYSTEM_CONFIG_DIR)}")

    # Set proper permissions
    set_system_permissions()
    print("Set proper directory permissions.")

    # Setup systemd service
    service_ok, service_error = setup_systemd_service()
    if not service_ok:
        sys.exit(service_error)
    print(f"Installed service file to: {SYSTEMD_DIR / SERVICE_FILENAME}")

    # Start service if requested
    service_started = False
    if start_service:
        service_started = manage_systemd_service("start")
        if service_started:
            print("Nexus service enabled and started.")
        else:
            print("Failed to start Nexus service.")

    # Write version marker
    current_version = write_installation_marker("system", service_started)
    print(f"Installed version {current_version}")

    print("\nSystem installation complete.")
    print("To uninstall: nexus-service uninstall")
    print("To start/stop: sudo systemctl start/stop nexus_service")
    print("To check status: systemctl status nexus_service")


def install_user(interactive: bool = True, config_file: pl.Path | None = None, force: bool = False) -> None:
    """Install Nexus service in user mode."""
    # Check for existing installation
    info = get_installation_info()
    if info.install_mode != "none" and not force:
        if info.install_mode == "user":
            print(f"Nexus service is already installed in user mode (version {info.version}).")
            return
        else:
            print(f"Nexus service is already installed in system mode (version {info.version}).")
            print("Please uninstall the system installation first or use --force.")
            return

    # Check dependencies
    deps_ok, deps_error = verify_external_dependencies()
    if not deps_ok:
        sys.exit(f"Missing dependencies: {deps_error}")

    print("Installing Nexus service in user mode...")

    # Create user directories
    create_directories(USER_CONFIG_DIR)
    print(f"Created user directory: {USER_CONFIG_DIR}")

    # Set up configuration
    _config = setup_config(USER_CONFIG_DIR, interactive, config_file)
    _env = env.NexusServiceEnv()
    create_persistent_directory(_config, _env)
    print(f"Created configuration at: {config.get_config_path(USER_CONFIG_DIR)}")

    # Write version marker
    current_version = write_installation_marker("user")
    print(f"Installed version {current_version}")

    print("\nUser installation complete.")
    print("To uninstall: nexus-service uninstall")
    print("To start the service: nexus-service")
    print("To check status: nexus-service status")


def uninstall(keep_config: bool = False, force: bool = False) -> None:
    """Uninstall Nexus service."""
    # Check installation type
    info = get_installation_info()

    if info.install_mode == "none" and not force:
        print("Nexus service is not installed.")
        return

    if info.install_mode == "system":
        require_root()
        print("Uninstalling system installation...")

        # Stop service
        if manage_systemd_service("stop"):
            print("Nexus service stopped and disabled.")

        # Remove service files
        remove_service_files()
        print(f"Removed service file: {SYSTEMD_DIR / SERVICE_FILENAME}")

        # Clean up files
        cleanup_files("system", keep_config)
        if keep_config:
            print(f"Kept configuration directory: {SYSTEM_CONFIG_DIR}")
        else:
            print(f"Removed directory: {SYSTEM_CONFIG_DIR}")

        # Remove user
        if remove_service_user():
            print(f"Removed {SERVICE_USER} system user.")

    elif info.install_mode == "user":
        print("Uninstalling user installation...")
        cleanup_files("user", keep_config)
        if keep_config:
            print(f"Kept configuration directory: {USER_CONFIG_DIR}")
        else:
            print(f"Removed directory: {USER_CONFIG_DIR}")

    print("\nNexus service has been uninstalled.")


#### IDK WHERE THESE SOHULD GO


def get_service_directory() -> tuple[pl.Path | None, bool]:
    """Determine service directory and whether this is first run."""
    info = get_installation_info()

    if info.install_mode == "system":
        return SYSTEM_CONFIG_DIR, False
    elif info.install_mode == "user":
        return USER_CONFIG_DIR, False

    # Check for user config without marker
    user_config_dir = pl.Path.home() / ".nexus_service"
    if (user_config_dir / "config.toml").exists():
        return user_config_dir, False

    # No installation found
    return None, True


def get_valid_config(service_dir: pl.Path | None) -> config.NexusServiceConfig:
    """Get configuration from file or defaults."""
    if service_dir and (service_dir / "config.toml").exists():
        try:
            return config.load_config(service_dir)
        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default configuration")

    return config.NexusServiceConfig(service_dir=service_dir)


def prompt_installation_mode() -> None:
    """Prompt user for installation mode and perform setup."""
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


def command_status() -> None:
    """Show current installation status."""
    info = get_installation_info()

    print("\nNexus Service Status")
    print("===================")

    # Installation status
    if info.install_mode == "none":
        print("Installation: Not installed")
    else:
        print(f"Installation: {info.install_mode.capitalize()} mode")
        print(f"Version: {info.version}")
        print(f"Installed on: {info.install_date}")
        if info.installed_by:
            print(f"Installed by: {info.installed_by}")
        print(f"Service directory: {info.install_path}")

    # Service status (for system installation)
    if info.install_mode == "system":
        try:
            import subprocess

            result = subprocess.run(["systemctl", "is-active", "nexus.service"], capture_output=True, text=True)
            is_active = result.stdout.strip() == "active"

            result = subprocess.run(["systemctl", "is-enabled", "nexus.service"], capture_output=True, text=True)
            is_enabled = result.stdout.strip() == "enabled"

            print(f"Service active: {'Yes' if is_active else 'No'}")
            print(f"Service enabled: {'Yes' if is_enabled else 'No'}")
        except Exception as e:
            print(f"Error checking service status: {e}")


def display_config(_config: config.NexusServiceConfig) -> None:
    print("======================")
    for key, value in _config.model_dump().items():
        print(f"{key}: {value}")
    print("\n")


def command_config() -> None:
    """Show current configuration."""
    info = get_installation_info()

    # Get configuration
    if info.install_mode == "none":
        print("Nexus service is not installed. Using default configuration.")
        config_obj = config.NexusServiceConfig(service_dir=None)
    else:
        if not info.config_path or not info.config_path.exists():
            print(f"Configuration file not found at expected location: {info.config_path}")
            return
        config_obj = config.load_config(info.install_path)

    display_config(config_obj)


def create_argument_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Nexus Service: GPU Job Management Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nexus-service                      # Run the service
  nexus-service install              # Install as system service (default)
  nexus-service install --user       # Install for current user only
  nexus-service uninstall            # Remove installation
  nexus-service config               # Show current configuration
  nexus-service status               # Check service status
""",
    )

    # Create subparsers for commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Install command
    install_parser = subparsers.add_parser("install", help="Install Nexus service")
    install_parser.add_argument(
        "--user", action="store_true", help="Install for current user only (default: system installation)"
    )
    install_parser.add_argument("--config", help="Path to config file for non-interactive setup")
    install_parser.add_argument("--no-interactive", action="store_true", help="Skip interactive configuration")
    install_parser.add_argument("--force", action="store_true", help="Force installation even if already installed")
    install_parser.add_argument("--no-start", action="store_true", help="Don't start service after installation")

    # Uninstall command
    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall Nexus service")
    uninstall_parser.add_argument(
        "--keep-config", action="store_true", help="Keep configuration files when uninstalling"
    )
    uninstall_parser.add_argument("--force", action="store_true", help="Force uninstallation even if not installed")

    # Config and status commands
    subparsers.add_parser("config", help="Show Nexus service configuration")
    subparsers.add_parser("status", help="Show Nexus service status")

    return parser


def initialize_service(service_dir: pl.Path | None) -> context.NexusServiceContext:
    """Initialize service components and create context."""
    # Initialize configuration
    _config = get_valid_config(service_dir)
    _env = env.NexusServiceEnv()

    # Setup database path and log directory
    db_path = ":memory:" if _config.service_dir is None else str(config.get_db_path(_config.service_dir))
    log_dir = None if _config.service_dir is None else config.get_log_dir(_config.service_dir)

    # Create persistent directories if needed
    if _config.service_dir is not None:
        create_persistent_directory(_config, _env=_env)

    # Initialize logger and database
    _logger = logger.create_service_logger(log_dir, name="nexus_service", log_level=_config.log_level)
    _db = db.create_connection(_logger, db_path=db_path)

    # Create service context
    return context.NexusServiceContext(db=_db, config=_config, env=_env, logger=_logger)
