"""
# never installed before:
#
`nexus-service`
setup user, install systemd, setup multiuser screen

setup configuration, optionally non interactive somehow
setup dirs
service now running as systemd


uninstall:
delete configuration
uninstall user and systemd service
remove dirs

upgrade:
uninstall systemd, mu screen, and user
reinstall systemd, mu screen, and user

tests:
setup dirs
"""

import os
import pathlib as pl
import pwd
import shutil
import subprocess
import sys

from nexus.service.core import config, env

__all__ = ["install", "uninstall", "verify_external_dependencies"]

MARKER_FILE = pl.Path("/etc/nexus_service/nexus_service_installed")
SYSTEMD_DIR = pl.Path("/etc/systemd/system")
SERVICE_FILENAME = "nexus_service.service"
SCRIPT_DIR = pl.Path(__file__).parent


def get_installed_version() -> str | None:
    if MARKER_FILE.exists():
        try:
            return MARKER_FILE.read_text().strip()
        except Exception:
            return None
    return None


def already_installed() -> bool:
    return MARKER_FILE.exists()


def require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("This operation requires root privileges. Please run as sudo or as root.")


def fetch_latest_version() -> tuple[bool, str, str | None]:
    try:
        import requests

        r = requests.get("https://pypi.org/pypi/nexusai/json", timeout=2)
        r.raise_for_status()
        data = r.json()
        return True, data["info"]["version"], None
    except Exception as e:
        return False, "", str(e)


def verify_external_dependencies() -> tuple[bool, str | None]:
    if shutil.which("git") is None:
        return False, "Git is not installed or not in PATH."
    if shutil.which("screen") is None:
        return False, "Screen is not installed or not in PATH."
    return True, None


def create_nexus_service_directory() -> None:
    nexus_service_dir = pl.Path("/etc/nexus_service")
    nexus_service_dir.mkdir(parents=True, exist_ok=True)


def create_nexus_service_user() -> bool:
    try:
        pwd.getpwnam("nexus_service")
        return False
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", "nexus_service"], check=True)
        return True


def configure_multiuser_screen() -> bool:
    nexus_service_screenrc = pl.Path("/home/nexus_service/.screenrc")
    if not nexus_service_screenrc.exists():
        nexus_service_screenrc.write_text("multiuser on\nacladd .\n")
        subprocess.run(["chown", "nexus_service:nexus_service", str(nexus_service_screenrc)], check=True)
        return True
    return False


def setup_systemd_service() -> tuple[bool, str | None]:
    source_service = SCRIPT_DIR / SERVICE_FILENAME
    if not source_service.exists():
        return False, f"Service file {source_service} not found!"

    dest_service = SYSTEMD_DIR / SERVICE_FILENAME
    shutil.copy(source_service, dest_service)
    return True, None


def manage_systemd_service(action: str) -> None:
    if action == "start":
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", SERVICE_FILENAME], check=True)
        subprocess.run(["systemctl", "start", SERVICE_FILENAME], check=True)
    elif action == "stop":
        subprocess.run(["systemctl", "stop", SERVICE_FILENAME], check=False)
        subprocess.run(["systemctl", "disable", SERVICE_FILENAME], check=False)
        subprocess.run(["systemctl", "daemon-reload"], check=True)


def write_version_marker() -> str:
    from importlib.metadata import version as get_version

    current_version = get_version("nexusai")
    MARKER_FILE.write_text(current_version)
    os.chmod(MARKER_FILE, 0o644)
    return current_version


def remove_service_files() -> None:
    service_file = SYSTEMD_DIR / SERVICE_FILENAME
    if service_file.exists():
        service_file.unlink()


def cleanup_nexus_service_files() -> None:
    nexus_service_dir = pl.Path("/etc/nexus_service")
    if nexus_service_dir.exists():
        shutil.rmtree(nexus_service_dir, ignore_errors=True)

    home_config = pl.Path.home() / ".nexus_service_service"
    if home_config.exists():
        shutil.rmtree(home_config, ignore_errors=True)


def remove_nexus_service_user() -> bool:
    try:
        pwd.getpwnam("nexus_service")
        subprocess.run(["userdel", "-r", "nexus_service"], check=True)
        return True
    except KeyError:
        return False


def create_persistent_directory(_config: config.NexusServiceConfig, _env: env.NexusServiceEnv) -> None:
    _config.service_dir.mkdir(parents=True, exist_ok=True)

    # Create the environment file if it doesn't exist
    if not config.get_env_path(_config.service_dir).exists():
        env.save_env(_env, env_path=config.get_env_path(_config.service_dir))

    # Ensure the jobs directory exists
    config.get_jobs_dir(_config.service_dir).mkdir(parents=True, exist_ok=True)

    # Create the configuration file if it doesn't exist
    if not config.get_config_path(_config.service_dir).exists():
        config.save_config(_config)


def install():
    require_root()

    if already_installed():
        print("Nexus service is already installed. Upgrading if needed.")

    # Create directory
    create_nexus_service_directory()
    print(f"Created directory: {pl.Path('/etc/nexus_service')}")

    # Create user
    if create_nexus_service_user():
        print("Created nexus_service system user.")
    else:
        print("User 'nexus_service' already exists.")

    # Configure screen
    if configure_multiuser_screen():
        print("Configured multiuser Screen for nexus_service user.")

    # Setup systemd
    service_ok, service_error = setup_systemd_service()
    if not service_ok:
        sys.exit(service_error)
    print(f"Copied service file to: {SYSTEMD_DIR / SERVICE_FILENAME}")

    # Start service
    manage_systemd_service("start")
    print("Nexus service enabled and started.")

    # Write version
    current_version = write_version_marker()
    print(f"Created marker file with version {current_version}: {MARKER_FILE}")
    print("To uninstall, run: nexus-service uninstall")

    # Check for updates
    update_ok, latest_version, error = fetch_latest_version()
    if update_ok and latest_version != current_version:
        print(f"A newer version of nexusai ({latest_version}) is available on PyPI. Current: {current_version}")
    elif not update_ok:
        print(f"Failed to check for new version: {error}")


def uninstall():
    require_root()

    if not already_installed():
        print("Nexus service is not installed.")
        return

    # Stop service
    manage_systemd_service("stop")
    print("Nexus service stopped and disabled.")

    # Remove service files
    remove_service_files()
    print(f"Removed service file: {SYSTEMD_DIR / SERVICE_FILENAME}")

    # Clean up files
    cleanup_nexus_service_files()
    print(f"Removed directory: {pl.Path('/etc/nexus_service')}")
    print(f"Removed home configuration directory: {pl.Path.home() / '.nexus_service_service'}")

    # Remove user
    if remove_nexus_service_user():
        print("Removed nexus_service system user.")
    else:
        print("User 'nexus_service' does not exist; skipping removal.")

    # Remove marker
    if MARKER_FILE.exists():
        MARKER_FILE.unlink()
        print(f"Removed marker file: {MARKER_FILE}")

    print("Nexus service has been uninstalled.")
