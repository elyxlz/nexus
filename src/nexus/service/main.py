import argparse
import asyncio
import contextlib
import importlib.metadata
import os
import pathlib as pl
import sys

import fastapi as fa
import uvicorn

from nexus.service.api import router, scheduler
from nexus.service.core import config, context, db, env, logger
from nexus.service.installation import setup


def create_app(ctx: context.NexusServiceContext) -> fa.FastAPI:
    app = fa.FastAPI(
        title="Nexus GPU Job Service",
        description="GPU Job Management Service",
        version=importlib.metadata.version("nexusai"),
    )
    app.state.ctx = ctx

    @contextlib.asynccontextmanager
    async def lifespan(app: fa.FastAPI):
        ctx.logger.info("scheduler starting")
        scheduler_task = asyncio.create_task(scheduler.scheduler_loop(ctx=app.state.ctx))
        try:
            yield
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

            ctx.db.close()
            ctx.logger.info("Nexus service stopped")

    app.router.lifespan_context = lifespan
    app.include_router(router.router)

    return app


def check_installation() -> tuple[pl.Path | None, bool]:
    """
    Check if Nexus service is installed and determine service directory.

    Returns:
        tuple of (service_dir, first_run)
        - service_dir: Path to service directory or None if running stateless
        - first_run: True if this is the first run (no installation detected)
    """
    info = setup.get_installation_info()

    if info.install_mode == "system":
        return setup.SYSTEM_CONFIG_DIR, False
    elif info.install_mode == "user":
        return setup.USER_CONFIG_DIR, False

    # Not installed, but check if config exists in default location
    user_config_dir = pl.Path.home() / ".nexus_service"
    user_config_path = user_config_dir / "config.toml"

    if user_config_path.exists():
        return user_config_dir, False

    # No installation or config found
    return None, True


def get_valid_config(service_dir: pl.Path | None) -> config.NexusServiceConfig:
    """
    Get a valid configuration from existing config or defaults.

    Args:
        service_dir: Directory where service files are stored, or None for stateless mode

    Returns:
        A valid NexusServiceConfig instance
    """
    if service_dir and (service_dir / "config.toml").exists():
        try:
            # Load existing config
            return config.load_config(service_dir)
        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default configuration")

    # Use default config
    return config.NexusServiceConfig(service_dir=service_dir)


def setup_first_run() -> None:
    """
    Handle first run detection and interactive setup.
    """
    print("First run detected. Nexus service is not installed.")
    print("You can run in the following modes:")
    print("  1. Install as system service (requires sudo)")
    print("  2. Install for current user only")
    print("  3. Run without installing (stateless)")

    try:
        choice = input("Select mode [1-3, default=1]: ").strip()

        if choice == "2":
            setup.install_user(interactive=True)
            sys.exit(0)
        elif choice == "3":
            print("Running in stateless mode...")
            return
        else:  # Default or choice "1"
            setup.install_system(interactive=True)
    except KeyboardInterrupt:
        print("\nSetup cancelled")
        os._exit(0)


def run_service(service_dir: pl.Path | None) -> None:
    """Run the Nexus service with the specified configuration."""
    # Initialize configuration
    _config = get_valid_config(service_dir)
    _env = env.NexusServiceEnv()

    # Setup database path and log directory
    db_path = ":memory:"
    log_dir = None

    if _config.service_dir is not None:
        # Ensure persistent directories exist
        setup.create_persistent_directory(_config, _env=_env)
        db_path = str(config.get_db_path(_config.service_dir))
        log_dir = config.get_log_dir(_config.service_dir)

    # Initialize logger and database
    _logger = logger.create_service_logger(log_dir, name="nexus_service", log_level=_config.log_level)
    _db = db.create_connection(_logger, db_path=db_path)

    # Create service context
    ctx = context.NexusServiceContext(db=_db, config=_config, env=_env, logger=_logger)

    # Start FastAPI application
    app = create_app(ctx)
    uvicorn.run(app, host=_config.host, port=_config.port, log_level=_config.log_level)


def cmd_install(args: argparse.Namespace) -> None:
    """Handle install command."""
    config_file = None
    if args.config:
        config_file = pl.Path(args.config)
        if not config_file.exists():
            sys.exit(f"Config file not found: {args.config}")

    if args.user:
        setup.install_user(interactive=not args.no_interactive, config_file=config_file, force=args.force)
    else:
        # Default to system installation
        setup.install_system(
            interactive=not args.no_interactive,
            config_file=config_file,
            start_service=not args.no_start,
            force=args.force,
        )


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Handle uninstall command."""
    setup.uninstall(keep_config=args.keep_config, force=args.force)


def cmd_config(args: argparse.Namespace) -> None:
    """Handle config command."""
    info = setup.get_installation_info()

    # Show current configuration
    if info.install_mode == "none":
        print("Nexus service is not installed. Using default configuration.")
        config_obj = config.NexusServiceConfig(service_dir=None)
    else:
        config_path = info.config_path
        if not config_path or not config_path.exists():
            print(f"Configuration file not found at expected location: {config_path}")
            return
        config_obj = config.load_config(info.install_path)

    # Display config
    print("\nCurrent Configuration:")
    print("======================")
    for key, value in config_obj.model_dump().items():
        print(f"{key}: {value}")


def cmd_status() -> None:
    """Handle status command."""
    info = setup.get_installation_info()

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
        print(f"Config directory: {info.install_path}")

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


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for nexus-service command."""
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

    # Config command
    subparsers.add_parser("config", help="Show Nexus service configuration")

    # Status command
    subparsers.add_parser("status", help="Show Nexus service status")

    return parser


def main():
    # Parse command line arguments
    parser = create_parser()
    args = parser.parse_args()

    # Check version and notify about updates
    try:
        current_version = importlib.metadata.version("nexusai")
        success, remote_version, error = setup.fetch_latest_version()
        if success and remote_version < current_version:
            print(f"New version available: {remote_version} (current: {current_version})")
    except Exception:
        # Skip version check on failure
        pass

    # Check for external dependencies
    deps_ok, deps_error = setup.verify_external_dependencies()
    if not deps_ok:
        sys.exit(f"Missing dependencies: {deps_error}")

    # Handle commands
    if args.command == "install":
        cmd_install(args)
        return
    elif args.command == "uninstall":
        cmd_uninstall(args)
        return
    elif args.command == "config":
        cmd_config(args)
        return
    elif args.command == "status":
        cmd_status()
        return

    # If no command provided, run the service
    # Check for installation status
    service_dir, first_run = check_installation()

    # Handle first run setup if no explicit command was given
    if first_run and not args.command:
        setup_first_run()

    # Run the service
    run_service(service_dir)
