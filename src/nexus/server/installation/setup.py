import argparse
import dataclasses as dc
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

from nexus.server.core import config, context, db, logger

SYSTEM_SERVER_DIR = pl.Path("/etc/nexus_server")
USER_SERVER_DIR = pl.Path.home() / ".nexus_server"
SERVER_USER = "nexus"
SYSTEMD_DIR = pl.Path("/etc/systemd/system")
SYSTEMD_SERVICE_FILENAME = "nexus-server.service"

MARKER_SYSTEM = SYSTEM_SERVER_DIR / "nexus_server.json"
MARKER_USER = USER_SERVER_DIR / "nexus_server.json"


@dc.dataclass(frozen=True)
class InstallationInfo:
    version: str
    install_date: str
    install_mode: tp.Literal["system", "user", "none"] = "none"
    install_path: pl.Path | None = None
    config_path: pl.Path | None = None
    installed_by: str | None = None
    server_enabled: bool = False


def get_installation_info() -> InstallationInfo:
    # Check system installation but handle permission errors gracefully
    try:
        if MARKER_SYSTEM.exists():
            try:
                data = json.loads(MARKER_SYSTEM.read_text())
                return InstallationInfo(
                    version=data.get("version", "unknown"),
                    install_date=data.get("install_date", "unknown"),
                    install_mode="system",
                    install_path=SYSTEM_SERVER_DIR,
                    config_path=SYSTEM_SERVER_DIR / "config.toml",
                    installed_by=data.get("installed_by"),
                    server_enabled=data.get("server_enabled", False),
                )
            except Exception:
                pass
    except PermissionError:
        # Log the permission error but continue checking user installation
        pass

    # Check user installation
    try:
        if MARKER_USER.exists():
            try:
                data = json.loads(MARKER_USER.read_text())
                return InstallationInfo(
                    version=data.get("version", "unknown"),
                    install_date=data.get("install_date", "unknown"),
                    install_mode="user",
                    install_path=USER_SERVER_DIR,
                    config_path=USER_SERVER_DIR / "config.toml",
                    installed_by=data.get("installed_by"),
                    server_enabled=False,
                )
            except Exception:
                pass
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


def get_server_directory() -> pl.Path | None:
    info = get_installation_info()

    if info.install_mode == "system":
        return SYSTEM_SERVER_DIR
    elif info.install_mode == "user":
        return USER_SERVER_DIR

    if (USER_SERVER_DIR / "config.toml").exists():
        return USER_SERVER_DIR

    return None


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


def write_installation_marker(mode: tp.Literal["system", "user"], server_enabled: bool = False) -> str:
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
        "server_enabled": server_enabled,
    }

    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(install_data, indent=2))
    os.chmod(marker_path, 0o644)

    return current_version


def create_directories(server_dir: pl.Path) -> None:
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "logs").mkdir(parents=True, exist_ok=True)
    (server_dir / "jobs").mkdir(parents=True, exist_ok=True)


def create_server_user() -> bool:
    try:
        pwd.getpwnam(SERVER_USER)
        return False
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", SERVER_USER], check=True)
        return True


def configure_multiuser_screen() -> bool:
    screenrc_path = pl.Path(f"/home/{SERVER_USER}/.screenrc")
    if not screenrc_path.exists():
        screenrc_path.write_text("multiuser on\nacladd .\n")
        subprocess.run(["chown", f"{SERVER_USER}:{SERVER_USER}", str(screenrc_path)], check=True)
        return True
    return False


def setup_shared_screen_dir() -> bool:
    """Create a shared screen socket directory that all users can access."""
    screen_dir = pl.Path("/tmp/screen_nexus")
    if not screen_dir.exists():
        screen_dir.mkdir(parents=True, exist_ok=True)
        # Mode 1777 = sticky bit + rwxrwxrwx (world-writable with sticky bit)
        os.chmod(screen_dir, 0o1777)
        return True
    return False


def set_system_permissions() -> None:
    subprocess.run(["chown", "-R", f"{SERVER_USER}:{SERVER_USER}", str(SYSTEM_SERVER_DIR)], check=True)
    subprocess.run(["chmod", "-R", "770", str(SYSTEM_SERVER_DIR)], check=True)


def setup_systemd_server() -> tuple[bool, str | None]:
    from nexus.server.installation import systemd

    server_content = systemd.get_service_file_content()
    dest_server = SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME
    dest_server.write_text(server_content)
    return True, None


def manage_systemd_server(action: str) -> bool:
    try:
        if action == "start":
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", SYSTEMD_SERVICE_FILENAME], check=True)
            subprocess.run(["systemctl", "start", SYSTEMD_SERVICE_FILENAME], check=True)
            return True
        elif action == "stop":
            subprocess.run(["systemctl", "stop", SYSTEMD_SERVICE_FILENAME], check=False)
            subprocess.run(["systemctl", "disable", SYSTEMD_SERVICE_FILENAME], check=False)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            return True
        return False
    except subprocess.CalledProcessError:
        return False


def install_system_server() -> None:
    server_ok, server_error = setup_systemd_server()
    if not server_ok:
        sys.exit(server_error)
    print(f"Installed server file to: {SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME}")


def start_system_server() -> bool:
    server_started = manage_systemd_server("start")
    print("Nexus server " + ("enabled and started." if server_started else "failed to start."))
    return server_started


def stop_system_server() -> bool:
    server_stopped = manage_systemd_server("stop")
    print("Nexus server " + ("disabled and stopped." if server_stopped else "failed to stop."))
    return server_stopped


def create_interactive_config(default_config: config.NexusServerConfig) -> config.NexusServerConfig:
    print("\nNexus Server Configuration")
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

    return config.NexusServerConfig(
        server_dir=default_config.server_dir,
        host=host,
        port=port,
        node_name=node_name,
        log_level=log_level,
    )


def setup_config(
    server_dir: pl.Path, interactive: bool = True, config_file: pl.Path | None = None
) -> config.NexusServerConfig:
    default_config = config.NexusServerConfig(server_dir=server_dir)

    if config_file and config_file.exists():
        try:
            file_config = config.load_config(server_dir)
            file_config.server_dir = server_dir
            return file_config
        except Exception as e:
            print(f"Error loading config file: {e}")
            print("Falling back to default configuration.")

    if not interactive:
        return default_config

    return create_interactive_config(default_config)


def display_config(_config: config.NexusServerConfig) -> None:
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


def create_persistent_directory(_config: config.NexusServerConfig) -> None:
    if _config.server_dir is None:
        raise ValueError("Server directory cannot be None")

    _config.server_dir.mkdir(parents=True, exist_ok=True)
    config.get_jobs_dir(_config.server_dir).mkdir(parents=True, exist_ok=True)
    config.get_log_dir(_config.server_dir).mkdir(parents=True, exist_ok=True)

    config.save_config(_config)


def remove_installation_files(mode: tp.Literal["system", "user"], keep_config: bool) -> None:
    server_dir = SYSTEM_SERVER_DIR if mode == "system" else USER_SERVER_DIR
    if not keep_config and server_dir.exists():
        shutil.rmtree(server_dir, ignore_errors=True)
        print(f"Removed directory: {server_dir}")
    else:
        marker = MARKER_SYSTEM if mode == "system" else MARKER_USER
        if marker.exists():
            marker.unlink()
        if keep_config:
            print(f"Kept configuration directory: {server_dir}")


def remove_server_user() -> bool:
    try:
        pwd.getpwnam(SERVER_USER)
        subprocess.run(["userdel", "-r", SERVER_USER], check=True)
        return True
    except (KeyError, subprocess.CalledProcessError):
        return False


def remove_server_files() -> None:
    server_file = SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME
    if server_file.exists():
        server_file.unlink()


def remove_system_components() -> None:
    stop_system_server()
    remove_server_files()
    print(f"Removed server file: {SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME}")


def check_installation_prerequisites(mode: tp.Literal["system", "user"], force: bool = False) -> None:
    if importlib.util.find_spec("nexus") is None:
        sys.exit(
            "ERROR: The 'nexus' package is not available in the system Python environment.\n"
            "Install it with: sudo pip3 install nexusai"
        )

    info = get_installation_info()
    if info.install_mode != "none" and not force:
        if info.install_mode == mode:
            sys.exit(f"Nexus server is already installed in {mode} mode (version {info.version}).")
        else:
            other_mode = "user" if mode == "system" else "system"
            sys.exit(
                f"Nexus server is already installed in {other_mode} mode (version {info.version}).\n"
                f"Please uninstall the {other_mode} installation first or use --force."
            )


def prepare_system_environment() -> None:
    create_directories(SYSTEM_SERVER_DIR)
    print(f"Created system directory: {SYSTEM_SERVER_DIR}")

    if create_server_user():
        print(f"Created {SERVER_USER} system user.")
    else:
        print(f"User '{SERVER_USER}' already exists.")

    if configure_multiuser_screen():
        print(f"Configured multiuser Screen for {SERVER_USER} user.")

    if setup_shared_screen_dir():
        print("Created shared screen directory at /tmp/screen_nexus")


def print_installation_complete_message(mode: tp.Literal["system", "user"]) -> None:
    print(f"\n{mode.capitalize()} installation complete.")
    if mode == "system":
        print("To uninstall: nexus-server uninstall")
        print("To start/stop: sudo systemctl start/stop nexus-server")
        print("To check status: systemctl status nexus-server")
    else:
        print("To uninstall: nexus-server uninstall")
        print("To start the server: nexus-server")
        print("To check status: nexus-server status")


def install_system(
    interactive: bool = True, config_file: pl.Path | None = None, start_server: bool = True, force: bool = False
) -> None:
    require_root()
    verify_external_dependencies()
    check_installation_prerequisites("system", force)

    print("Installing Nexus server in system mode...")
    prepare_system_environment()

    _config = setup_config(SYSTEM_SERVER_DIR, interactive, config_file)
    create_persistent_directory(_config)
    print(f"Created configuration at: {config.get_config_path(SYSTEM_SERVER_DIR)}")

    set_system_permissions()
    print("Set proper directory permissions.")

    install_system_server()
    server_started = False
    if start_server:
        server_started = start_system_server()

    current_version = write_installation_marker("system", server_started)
    print(f"Installed version {current_version}")

    print_installation_complete_message("system")


def install_user(interactive: bool = True, config_file: pl.Path | None = None, force: bool = False) -> None:
    verify_external_dependencies()
    check_installation_prerequisites("user", force)

    print("Installing Nexus server in user mode...")
    create_directories(USER_SERVER_DIR)
    print(f"Created user directory: {USER_SERVER_DIR}")

    _config = setup_config(USER_SERVER_DIR, interactive, config_file)
    create_persistent_directory(_config)
    print(f"Created configuration at: {config.get_config_path(USER_SERVER_DIR)}")

    current_version = write_installation_marker("user")
    print(f"Installed version {current_version}")

    print_installation_complete_message("user")


def uninstall(keep_config: bool = False, force: bool = False) -> None:
    info = get_installation_info()

    if info.install_mode == "none" and not force:
        print("Nexus server is not installed.")
        return

    if info.install_mode == "system":
        require_root()
        print("Uninstalling system installation...")
        remove_system_components()
        remove_installation_files("system", keep_config)

        if remove_server_user():
            print(f"Removed {SERVER_USER} system user.")

    elif info.install_mode == "user":
        print("Uninstalling user installation...")
        remove_installation_files("user", keep_config)

    print("\nNexus server has been uninstalled.")


def command_status() -> None:
    info = get_installation_info()

    print("\nNexus Server Status")
    print("===================")

    if info.install_mode == "none":
        print("Installation: Not installed")
    else:
        print(f"Installation: {info.install_mode.capitalize()} mode")
        print(f"Version: {info.version}")
        print(f"Installed on: {info.install_date}")
        if info.installed_by:
            print(f"Installed by: {info.installed_by}")
        print(f"Server directory: {info.install_path}")

    if info.install_mode == "system":
        try:
            result = subprocess.run(
                ["systemctl", "is-active", SYSTEMD_SERVICE_FILENAME], capture_output=True, text=True
            )
            is_active = result.stdout.strip() == "active"

            result = subprocess.run(
                ["systemctl", "is-enabled", SYSTEMD_SERVICE_FILENAME], capture_output=True, text=True
            )
            is_enabled = result.stdout.strip() == "enabled"

            print(f"Server active: {'Yes' if is_active else 'No'}")
            print(f"Server enabled: {'Yes' if is_enabled else 'No'}")
        except Exception as e:
            print(f"Error checking server status: {e}")


def command_config(edit_mode: bool = False) -> None:
    info = get_installation_info()

    if info.install_mode == "none":
        print("Nexus server is not installed. Using default configuration.")
        config_obj = config.NexusServerConfig(server_dir=None)
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
        start_server = not getattr(args, "no_start", False)
        install_system(interactive=interactive, config_file=config_file, start_server=start_server, force=force)


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
    print("First run detected. Nexus server is not installed.")
    print("You can run in the following modes:")
    print("  1. Install as systemd service (requires sudo)")
    print("  2. Install for current user only")
    print("  3. Run without installing (stateless)")

    try:
        choice = input("Select mode [1-3, default=1]: ").strip()

        if choice == "1":
            install_system(interactive=True)
        if choice == "2":
            install_user(interactive=True)
            sys.exit(0)
        elif choice == "3":
            print("Running in stateless mode...")
            return
        else:
            install_system(interactive=True)

    except KeyboardInterrupt:
        print("\nSetup cancelled")
        sys.exit(0)


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nexus Server: GPU Job Management Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nexus-server                      # Run the server
  nexus-server install              # Install as system server
  nexus-server install --user       # Install for current user only
  nexus-server uninstall            # Remove installation
  nexus-server config               # Show current configuration
  nexus-server config --edit        # Edit configuration in text editor
  nexus-server status               # Check server status

Configuration can also be set using environment variables (prefix=NS_):
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    install_parser = subparsers.add_parser("install", help="Install Nexus server")
    install_parser.add_argument(
        "--user", action="store_true", help="Install for current user only (default: system installation)"
    )
    install_parser.add_argument("--config", help="Path to config file for non-interactive setup")
    install_parser.add_argument("--no-interactive", action="store_true", help="Skip interactive configuration")
    install_parser.add_argument("--force", action="store_true", help="Force installation even if already installed")
    install_parser.add_argument("--no-start", action="store_true", help="Don't start server after installation")

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall Nexus server")
    uninstall_parser.add_argument(
        "--keep-config", action="store_true", help="Keep configuration files when uninstalling"
    )
    uninstall_parser.add_argument("--force", action="store_true", help="Force uninstallation even if not installed")

    config_parser = subparsers.add_parser("config", help="Manage Nexus server configuration")
    config_parser.add_argument("--edit", action="store_true", help="Edit configuration in text editor")

    subparsers.add_parser("status", help="Show Nexus server status")

    return parser


def initialize_server(server_dir: pl.Path | None) -> context.NexusServerContext:
    if server_dir and (server_dir / "config.toml").exists():
        _config = config.load_config(server_dir)
        create_persistent_directory(_config)
    else:
        _config = config.NexusServerConfig(server_dir=server_dir)

    db_path = ":memory:" if _config.server_dir is None else str(config.get_db_path(_config.server_dir))
    log_dir = None if _config.server_dir is None else config.get_log_dir(_config.server_dir)

    _logger = logger.create_logger(log_dir, name="nexus_server", log_level=_config.log_level)
    _db = db.create_connection(_logger, db_path=db_path)

    return context.NexusServerContext(db=_db, config=_config, logger=_logger)
