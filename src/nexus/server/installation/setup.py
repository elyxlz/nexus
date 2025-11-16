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
import stat
import subprocess
import sys
import time
import typing as tp

from nexus.server.core import config, context, db

SYSTEM_SERVER_DIR = pl.Path("/etc/nexus_server")
SERVER_USER = "nexus"
SYSTEMD_DIR = pl.Path("/etc/systemd/system")
SYSTEMD_SERVICE_FILENAME = "nexus-server.service"

MARKER_SYSTEM = SYSTEM_SERVER_DIR / "nexus_server.json"


@dc.dataclass(frozen=True, slots=True)
class InstallationInfo:
    version: str
    install_date: str
    install_mode: tp.Literal["system", "none"] = "none"
    install_path: pl.Path | None = None
    config_path: pl.Path | None = None
    installed_by: str | None = None
    server_enabled: bool = False


def get_installation_info() -> InstallationInfo:
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

    return None


def require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("This operation requires root privileges. Please run with sudo.")


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


def write_installation_marker(server_enabled: bool = False) -> str:
    from datetime import datetime

    try:
        current_version = importlib.metadata.version("nexusai")
    except importlib.metadata.PackageNotFoundError:
        current_version = "unknown"

    install_data = {
        "version": current_version,
        "install_date": datetime.now().isoformat(),
        "install_mode": "system",
        "installed_by": getpass.getuser(),
        "server_enabled": server_enabled,
    }

    MARKER_SYSTEM.parent.mkdir(parents=True, exist_ok=True)
    MARKER_SYSTEM.write_text(json.dumps(install_data, indent=2))
    os.chmod(MARKER_SYSTEM, 0o644)

    return current_version


def create_directories(server_dir: pl.Path) -> None:
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "logs").mkdir(parents=True, exist_ok=True)
    (server_dir / "jobs").mkdir(parents=True, exist_ok=True)


def create_server_user(sup_groups: list[str] | None = None) -> bool:
    user_exists = False
    try:
        pwd.getpwnam(SERVER_USER)
        user_exists = True
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", SERVER_USER], check=True)

    if sup_groups:
        for grp in sup_groups:
            subprocess.run(["usermod", "-aG", grp, SERVER_USER], check=True)
        print(f"Added supplementary groups: {', '.join(sup_groups)} to user {SERVER_USER}.")

    return not user_exists


def setup_shared_screen_dir() -> bool:
    screen_dir = pl.Path("/tmp/screen_nexus")
    if not screen_dir.exists():
        screen_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(screen_dir, 0o1777)
        return True
    return False


def setup_passwordless_nexus_attach() -> bool:
    sudoers_file = pl.Path("/etc/sudoers.d/nexus_attach")
    content = "ALL ALL=(nexus) NOPASSWD: /usr/bin/screen -r *\n"

    try:
        sudoers_file.write_text(content)
        os.chmod(sudoers_file, 0o440)
        return True
    except Exception:
        return False


def set_system_permissions() -> None:
    subprocess.run(["chown", "-R", f"{SERVER_USER}:{SERVER_USER}", str(SYSTEM_SERVER_DIR)], check=True)
    subprocess.run(["chmod", "-R", "770", str(SYSTEM_SERVER_DIR)], check=True)


def detect_nexus_server_executable() -> str:
    exec_path = shutil.which("nexus-server")
    if not exec_path:
        sys.exit(
            "ERROR: nexus-server executable not found in PATH.\n"
            "Ensure nexusai is installed correctly with: sudo pip3 install nexusai"
        )
    return exec_path


def setup_systemd_server(sup_groups: list[str] | None = None) -> tuple[bool, str | None]:
    from nexus.server.installation import systemd

    exec_path = detect_nexus_server_executable()
    server_content = systemd.get_service_file_content(exec_path, sup_groups)
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


def install_system_server(sup_groups: list[str] | None = None) -> None:
    server_ok, server_error = setup_systemd_server(sup_groups)
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

    node_name = input(f"Node name [default: {default_config.node_name}]: ").strip() or default_config.node_name

    port_input = input(f"Port [default: {default_config.port}]: ").strip()
    port = int(port_input) if port_input else default_config.port

    groups_input = input("Unix groups for nexus user (comma-separated, or leave empty for none): ").strip()
    sup_groups = [grp.strip() for grp in groups_input.split(",") if grp.strip()] if groups_input else []

    return config.NexusServerConfig(
        server_dir=default_config.server_dir,
        port=port,
        node_name=node_name,
        supplementary_groups=sup_groups,
    )


def setup_config(
    server_dir: pl.Path, interactive: bool = True, config_file: pl.Path | None = None
) -> config.NexusServerConfig:
    default_config = config.NexusServerConfig(server_dir=server_dir)

    if config_file and config_file.exists():
        try:
            file_config = config.load_config(server_dir)
            return file_config.model_copy(update={"server_dir": server_dir})
        except Exception as e:
            print(f"Error loading config file: {e}")
            print("Falling back to default configuration.")

    base_config = create_interactive_config(default_config) if interactive else default_config

    return base_config


def display_config(_config: config.NexusServerConfig) -> None:
    print("\nCurrent Configuration")
    print("======================")

    config_dict = _config.model_dump()

    for key, value in config_dict.items():
        print(f"{key}: {value}")

    external_ip = None
    try:
        result = subprocess.run(["curl", "-s", "ifconfig.me"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            external_ip = result.stdout.strip()
    except Exception:
        pass

    if external_ip:
        print(f"\nexternal_ip: {external_ip}")

    print("\n🔒 Authentication: SSH tunnel-based (no API token)")
    print("   Remote access requires SSH port forwarding")

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
    config.save_config(_config)


def remove_installation_files(keep_config: bool) -> None:
    if not keep_config and SYSTEM_SERVER_DIR.exists():
        shutil.rmtree(SYSTEM_SERVER_DIR, ignore_errors=True)
        print(f"Removed directory: {SYSTEM_SERVER_DIR}")
    else:
        if MARKER_SYSTEM.exists():
            MARKER_SYSTEM.unlink()
        if keep_config:
            print(f"Kept configuration directory: {SYSTEM_SERVER_DIR}")


def check_running_processes() -> list[int]:
    try:
        result = subprocess.run(["pgrep", "-u", SERVER_USER], capture_output=True, text=True)
        if result.returncode == 0:
            return [int(pid) for pid in result.stdout.strip().split()]
        return []
    except Exception:
        return []


def terminate_user_processes(yes_flag: bool = False) -> bool:
    pids = check_running_processes()
    if not pids:
        return True

    if not yes_flag:
        print(f"\n⚠️  WARNING: Found {len(pids)} processes running as the {SERVER_USER} user.")
        print(f"These processes must be terminated before removing the {SERVER_USER} user.")
        user_input = input("Do you want to terminate these processes? [y/N]: ").strip().lower()
        if user_input != "y":
            print("Aborted. Please terminate the processes manually and try again.")
            return False

    try:
        subprocess.run(["pkill", "-TERM", "-u", SERVER_USER], check=False)
        time.sleep(1)
        remaining_pids = check_running_processes()
        if remaining_pids:
            if yes_flag or input("Some processes are still running. Use force kill? [y/N]: ").strip().lower() == "y":
                subprocess.run(["pkill", "-KILL", "-u", SERVER_USER], check=False)
                time.sleep(0.5)
                if check_running_processes():
                    print("⚠️  Some processes could not be terminated. Please check manually.")
                    return False
            else:
                print("Aborted. Please terminate the processes manually and try again.")
                return False
        return True
    except Exception as e:
        print(f"Error terminating processes: {e}")
        return False


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
        print(f"Removed systemd service file: {server_file}")

    sudoers_file = pl.Path("/etc/sudoers.d/nexus_attach")
    if sudoers_file.exists():
        sudoers_file.unlink()
        print("Removed passwordless sudo access rule from /etc/sudoers.d/nexus_attach")


def remove_system_components() -> None:
    stop_system_server()
    remove_server_files()


def check_editable_install() -> bool:
    try:
        dist = importlib.metadata.distribution("nexusai")
        direct_url_file = dist.read_text("direct_url.json")
        if direct_url_file:
            direct_url_data = json.loads(direct_url_file)
            return direct_url_data.get("dir_info", {}).get("editable", False)
    except Exception:
        pass
    return False


def check_installation_prerequisites(force: bool = False) -> None:
    if importlib.util.find_spec("nexus") is None:
        sys.exit(
            "ERROR: The 'nexus' package is not available in the system Python environment.\n"
            "Install it with: sudo pip3 install nexusai"
        )

    if check_editable_install():
        sys.exit(
            "ERROR: nexusai is installed in editable mode (pip install -e).\n"
            "System installation requires a non-editable install.\n"
            "Uninstall with: sudo pip3 uninstall nexusai\n"
            "Then install with: sudo pip3 install nexusai"
        )

    info = get_installation_info()
    if info.install_mode == "system" and not force:
        sys.exit(f"Nexus server is already installed in system mode (version {info.version}).")


def setup_screen_run_dir() -> None:
    screen_dir = pl.Path("/run/screen")
    if not screen_dir.exists():
        screen_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created screen directory at {screen_dir}")

    current_perms = stat.S_IMODE(screen_dir.stat().st_mode)
    if current_perms != 0o777:
        screen_dir.chmod(0o777)
        print(f"Set permissions 777 on {screen_dir}")


def prepare_system_environment(sup_groups: list[str] | None = None) -> None:
    create_directories(SYSTEM_SERVER_DIR)
    print(f"Created system directory: {SYSTEM_SERVER_DIR}")

    if create_server_user(sup_groups):
        print(f"Created {SERVER_USER} system user.")
    else:
        print(f"User '{SERVER_USER}' already exists.")

    if setup_shared_screen_dir():
        print("Created shared screen directory at /tmp/screen_nexus")

    setup_screen_run_dir()

    if setup_passwordless_nexus_attach():
        print(
            "PASSWORDLESS ACCESS ENABLED: Any user can now run 'sudo -u nexus screen -r <session>' without a password"
        )
        print("Added rule to /etc/sudoers.d/nexus_attach")


def confirm_installation(_config: config.NexusServerConfig) -> bool:
    print("\n" + "=" * 80)
    print(" " * 25 + "INSTALLATION PREVIEW")
    print("=" * 80)

    print("\n📁 Files and Directories:")
    print(f"   Server directory:  {SYSTEM_SERVER_DIR}")
    print(f"   Config file:       {SYSTEM_SERVER_DIR / 'config.toml'}")
    print(f"   Database:          {SYSTEM_SERVER_DIR / 'nexus_server.db'}")
    print(f"   Systemd service:   {SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME}")

    print("\n⚙️  Configuration:")
    for key, value in _config.model_dump().items():
        print(f"   {key:20} {value}")

    print("\n" + "=" * 80)
    print("Note: Environment variables (NS_*) override config during installation")
    print("=" * 80)

    response = input("\nProceed with installation? [Y/n]: ").strip().lower()
    return response != "n"


def print_installation_complete_message(_config: config.NexusServerConfig) -> None:
    print("\n" + "=" * 80)
    print(" " * 25 + "INSTALLATION COMPLETE")
    print("=" * 80)

    print("\n🎉 Nexus Server is now installed and running!")

    print("\n📋 Server Details:")
    print(f"   Node name:    {_config.node_name}")

    print("\n💻 Remote Connection Info:")
    if _config.external_ip:
        print(f"   Address:      {_config.external_ip}")
    print(f"   Port:         {_config.port}")
    print("   Auth:         SSH tunnel (no API token needed)")

    print("\n   To connect from CLI, run:")
    print("   nx target add")
    print("   # Ensure your SSH key is in ~/.ssh/authorized_keys on this server")

    print("\n📝 View Config Anytime:")
    print("   sudo nexus-server config")

    print("\n🔧 Server Management:")
    print("   Start:   sudo nexus-server start")
    print("   Stop:    sudo nexus-server stop")
    print("   Restart: sudo nexus-server restart")
    print("   Status:  sudo nexus-server status")
    print("   Logs:    sudo nexus-server logs")

    print("\n📦 Uninstall:")
    print("   sudo nexus-server uninstall")

    print("\n" + "=" * 80)


def install_system(
    interactive: bool = True, config_file: pl.Path | None = None, start_server: bool = True, force: bool = False
) -> None:
    require_root()
    check_installation_prerequisites(force)

    print("Installing Nexus server in system mode...")

    _config = setup_config(SYSTEM_SERVER_DIR, interactive, config_file)

    if interactive:
        if not confirm_installation(_config):
            print("Installation cancelled.")
            return

    prepare_system_environment(_config.supplementary_groups)

    create_persistent_directory(_config)
    print(f"Created configuration at: {config.get_config_path(SYSTEM_SERVER_DIR)}")

    set_system_permissions()
    print("Set proper directory permissions.")

    server_ok, server_error = setup_systemd_server(_config.supplementary_groups)
    if not server_ok:
        sys.exit(server_error)
    print(f"Installed server file to: {SYSTEMD_DIR / SYSTEMD_SERVICE_FILENAME}")

    server_started = False
    if start_server:
        server_started = start_system_server()

    current_version = write_installation_marker(server_started)
    print(f"Installed version {current_version}")

    print_installation_complete_message(_config)


def uninstall(keep_config: bool = False, force: bool = False, yes: bool = False) -> None:
    info = get_installation_info()

    if info.install_mode == "none" and not force:
        print("Nexus server is not installed.")
        return

    if info.install_mode == "system":
        require_root()
        print("Uninstalling system installation...")
        remove_system_components()
        remove_installation_files(keep_config)

        if not terminate_user_processes(yes_flag=yes):
            print(f"\nCannot remove {SERVER_USER} user while processes are still running.")
            print("Please terminate these processes manually or run with --yes to force termination.")
            return

        if remove_server_user():
            print(f"Removed {SERVER_USER} system user.")

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
    start_server = not getattr(args, "no_start", False)
    install_system(interactive=interactive, config_file=config_file, start_server=start_server, force=force)


def handle_uninstall_command(args: argparse.Namespace) -> None:
    uninstall(keep_config=args.keep_config, force=args.force, yes=args.yes)


def handle_config_command(args: argparse.Namespace) -> None:
    command_config(edit_mode=args.edit)


def handle_logs_command(args: argparse.Namespace) -> None:
    """View nexus-server logs using journalctl."""
    try:
        cmd = ["journalctl", "-u", "nexus-server.service", "-f"]

        cmd.extend(["-n", str(args.lines)])

        subprocess.run(cmd)
    except Exception as e:
        print(f"Error viewing logs: {e}")
        sys.exit(1)


def handle_restart_command(args: argparse.Namespace) -> None:
    """Restart the nexus-server systemd service."""
    if os.geteuid() != 0:
        print("This operation requires root privileges. Please run with sudo.")
        sys.exit(1)

    try:
        print("Restarting nexus-server...")
        subprocess.run(["systemctl", "restart", "nexus-server.service"], check=True)
        print("Server restarted successfully.")
    except Exception as e:
        print(f"Error restarting server: {e}")
        sys.exit(1)


def handle_stop_command(args: argparse.Namespace) -> None:
    """Stop the nexus-server systemd service."""
    if os.geteuid() != 0:
        print("This operation requires root privileges. Please run with sudo.")
        sys.exit(1)

    try:
        print("Stopping nexus-server...")
        subprocess.run(["systemctl", "stop", "nexus-server.service"], check=True)
        print("Server stopped successfully.")
    except Exception as e:
        print(f"Error stopping server: {e}")
        sys.exit(1)


def handle_start_command(args: argparse.Namespace) -> None:
    """Start the nexus-server systemd service."""
    if os.geteuid() != 0:
        print("This operation requires root privileges. Please run with sudo.")
        sys.exit(1)

    try:
        print("Starting nexus-server...")
        subprocess.run(["systemctl", "start", "nexus-server.service"], check=True)
        print("Server started successfully.")
    except Exception as e:
        print(f"Error starting server: {e}")
        sys.exit(1)


def handle_command(args: argparse.Namespace) -> bool:
    command_handlers = {
        "install": handle_install_command,
        "uninstall": handle_uninstall_command,
        "config": handle_config_command,
        "status": lambda _: command_status(),
        "logs": handle_logs_command,
        "restart": handle_restart_command,
        "stop": handle_stop_command,
        "start": handle_start_command,
    }

    if args.command in command_handlers:
        command_handlers[args.command](args)
        return True

    return False


def prompt_installation_mode() -> None:
    print("First run detected. Nexus server is not installed.")
    print("You can run in the following modes:")
    print("  1. Install as systemd service (requires sudo)")
    print("  2. Run without installing (stateless)")

    try:
        choice = input("Select mode [1-2, default=1]: ").strip()

        if choice == "1":
            install_system(interactive=True)
        elif choice == "2":
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
  nexus-server uninstall            # Remove installation
  nexus-server config               # Show current configuration
  nexus-server config --edit        # Edit configuration in text editor
  nexus-server status               # Check server status
  nexus-server logs [-f] [-n N]     # View server logs (with optional follow and line count)
  nexus-server start                # Start the server
  nexus-server stop [-y]            # Stop the server (with optional skip confirmation)
  nexus-server restart [-y]         # Restart the server (with optional skip confirmation)
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    install_parser = subparsers.add_parser("install", help="Install Nexus server")
    install_parser.add_argument("--config", help="Path to config file for non-interactive setup")
    install_parser.add_argument("--no-interactive", action="store_true", help="Skip interactive configuration")
    install_parser.add_argument("--force", action="store_true", help="Force installation even if already installed")
    install_parser.add_argument("--no-start", action="store_true", help="Don't start server after installation")

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall Nexus server")
    uninstall_parser.add_argument(
        "--keep-config", action="store_true", help="Keep configuration files when uninstalling"
    )
    uninstall_parser.add_argument("--force", action="store_true", help="Force uninstallation even if not installed")
    uninstall_parser.add_argument(
        "--yes", "-y", action="store_true", help="Automatically terminate running processes without prompting"
    )

    config_parser = subparsers.add_parser("config", help="Manage Nexus server configuration")
    config_parser.add_argument("--edit", action="store_true", help="Edit configuration in text editor")

    subparsers.add_parser("status", help="Show Nexus server status")

    # Server management commands
    logs_parser = subparsers.add_parser("logs", help="View server logs")
    logs_parser.add_argument("-n", "--lines", type=int, default=50, help="Number of log lines to show")

    restart_parser = subparsers.add_parser("restart", help="Restart the server")
    restart_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    stop_parser = subparsers.add_parser("stop", help="Stop the server")
    stop_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    subparsers.add_parser("start", help="Start the server")

    return parser


def _check_screen_permissions() -> None:
    screen_dir = pl.Path("/run/screen")
    try:
        if not screen_dir.exists():
            screen_dir.mkdir(parents=True, exist_ok=True)
        if stat.S_IMODE(screen_dir.stat().st_mode) != 0o777:
            screen_dir.chmod(0o777)
    except (OSError, PermissionError) as e:
        try:
            print(f"Attempting to fix {screen_dir} permissions using sudo...")
            subprocess.run(["sudo", "mkdir", "-p", str(screen_dir)], check=True, capture_output=True)
            subprocess.run(["sudo", "chmod", "777", str(screen_dir)], check=True, capture_output=True)
            print(f"Successfully fixed {screen_dir} permissions.")
        except (subprocess.CalledProcessError, FileNotFoundError) as sudo_error:
            raise RuntimeError(
                f"Screen requires {screen_dir} with permissions 777. Error: {e}\n"
                f"Attempted automatic fix with sudo but failed: {sudo_error}\n"
                f"Manual fix: sudo mkdir -p {screen_dir} && sudo chmod 777 {screen_dir}"
            )


def initialize_context(server_dir: pl.Path | None) -> context.NexusServerContext:
    _check_screen_permissions()

    if server_dir and (server_dir / "config.toml").exists():
        _config = config.load_config(server_dir)
        create_persistent_directory(_config)
    else:
        _config = config.NexusServerConfig(server_dir=server_dir)

    db_path = ":memory:" if _config.server_dir is None else str(config.get_db_path(_config.server_dir))

    _db = db.create_connection(db_path=db_path)

    return context.NexusServerContext(db=_db, config=_config)
