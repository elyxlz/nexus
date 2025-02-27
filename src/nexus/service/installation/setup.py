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

import dataclasses as dc
import getpass
import importlib.metadata
import json
import os
import pathlib as pl
import pwd
import shutil
import subprocess
import sys
import typing as tp

from nexus.service.core import config, env

__all__ = [
    "install_system",
    "install_user",
    "uninstall",
    "setup_config",
    "verify_external_dependencies",
    "get_installation_info",
]

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

# Marker file that tracks installation state
MARKER_SYSTEM = SYSTEM_CONFIG_DIR / "nexus_service.json"
MARKER_USER = USER_CONFIG_DIR / "nexus_service.json"


@dc.dataclass(frozen=True)
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


def is_system_installed() -> bool:
    """Check if Nexus service is installed in system mode."""
    return MARKER_SYSTEM.exists()


def is_user_installed() -> bool:
    """Check if Nexus service is installed in user mode."""
    return MARKER_USER.exists()


def require_root() -> None:
    """Exit if not running as root."""
    if os.geteuid() != 0:
        sys.exit("This operation requires root privileges. Please run with sudo.")


# =========================================================================
# Version Management Functions
# =========================================================================


def fetch_latest_version() -> tuple[bool, str, str | None]:
    """Fetch the latest version from PyPI."""
    try:
        import requests

        r = requests.get("https://pypi.org/pypi/nexusai/json", timeout=2)
        r.raise_for_status()
        data = r.json()
        return True, data["info"]["version"], None
    except Exception as e:
        return False, "", str(e)


def write_installation_marker(mode: tp.Literal["system", "user"], service_enabled: bool = False) -> str:
    """Write installation marker file with metadata."""
    from datetime import datetime
    from importlib.metadata import version as get_version

    current_version = get_version("nexusai")
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
# Dependency and Prerequisites Checks
# =========================================================================


def verify_external_dependencies() -> tuple[bool, str | None]:
    """Verify that all external dependencies are installed."""
    if shutil.which("git") is None:
        return False, "Git is not installed or not in PATH."
    if shutil.which("screen") is None:
        return False, "Screen is not installed or not in PATH."
    return True, None


# =========================================================================
# System Installation Functions
# =========================================================================


def create_system_directories() -> None:
    """Create system directories for nexus service."""
    SYSTEM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (SYSTEM_CONFIG_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (SYSTEM_CONFIG_DIR / "jobs").mkdir(parents=True, exist_ok=True)


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


def set_system_permissions() -> None:
    """Set correct permissions for system directories."""
    # Make config directory accessible to service user
    subprocess.run(["chown", "-R", f"{SERVICE_USER}:{SERVICE_USER}", str(SYSTEM_CONFIG_DIR)], check=True)
    # Secure permissions: user+group read/write, others none
    subprocess.run(["chmod", "-R", "770", str(SYSTEM_CONFIG_DIR)], check=True)


# =========================================================================
# User Installation Functions
# =========================================================================


def create_user_directories() -> None:
    """Create user directories for nexus service."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (USER_CONFIG_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (USER_CONFIG_DIR / "jobs").mkdir(parents=True, exist_ok=True)


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
# Cleanup and Uninstallation
# =========================================================================


def remove_service_files() -> None:
    """Remove systemd service files."""
    service_file = SYSTEMD_DIR / SERVICE_FILENAME
    if service_file.exists():
        service_file.unlink()


def cleanup_system_files(keep_config: bool = False) -> None:
    """Clean up system files."""
    if not keep_config and SYSTEM_CONFIG_DIR.exists():
        shutil.rmtree(SYSTEM_CONFIG_DIR, ignore_errors=True)
    elif MARKER_SYSTEM.exists():
        MARKER_SYSTEM.unlink()


def cleanup_user_files(keep_config: bool = False) -> None:
    """Clean up user files."""
    if not keep_config and USER_CONFIG_DIR.exists():
        shutil.rmtree(USER_CONFIG_DIR, ignore_errors=True)
    elif MARKER_USER.exists():
        MARKER_USER.unlink()


def remove_service_user() -> bool:
    """Remove service user."""
    try:
        pwd.getpwnam(SERVICE_USER)
        subprocess.run(["userdel", "-r", SERVICE_USER], check=True)
        return True
    except (KeyError, subprocess.CalledProcessError):
        return False


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
        elif info.install_mode == "user":
            print(f"Nexus service is already installed in user mode (version {info.version}).")
            print("Please uninstall the user installation first or use --force.")
            return

    # Check dependencies
    deps_ok, deps_error = verify_external_dependencies()
    if not deps_ok:
        sys.exit(f"Missing dependencies: {deps_error}")

    print("Installing Nexus service in system mode...")

    # Create system directories
    create_system_directories()
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
        elif info.install_mode == "system":
            print(f"Nexus service is already installed in system mode (version {info.version}).")
            print("Please uninstall the system installation first or use --force.")
            return

    # Check dependencies
    deps_ok, deps_error = verify_external_dependencies()
    if not deps_ok:
        sys.exit(f"Missing dependencies: {deps_error}")

    print("Installing Nexus service in user mode...")

    # Create user directories
    create_user_directories()
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
        cleanup_system_files(keep_config)
        if keep_config:
            print(f"Kept configuration directory: {SYSTEM_CONFIG_DIR}")
        else:
            print(f"Removed directory: {SYSTEM_CONFIG_DIR}")

        # Remove user
        if remove_service_user():
            print(f"Removed {SERVICE_USER} system user.")

    elif info.install_mode == "user":
        print("Uninstalling user installation...")

        # Clean up files
        cleanup_user_files(keep_config)
        if keep_config:
            print(f"Kept configuration directory: {USER_CONFIG_DIR}")
        else:
            print(f"Removed directory: {USER_CONFIG_DIR}")

    print("\nNexus service has been uninstalled.")
