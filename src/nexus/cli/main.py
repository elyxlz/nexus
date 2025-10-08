import argparse
import importlib.metadata
import sys

# Import utils instead of colored directly, as we use utils.print_* helpers

from nexus.cli import api_client, config, jobs, setup, utils
from nexus.cli.config import NexusCliConfig

try:
    VERSION = importlib.metadata.version("nexusai")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"


def show_config(cfg: NexusCliConfig) -> None:
    try:
        utils.print_header("Current Configuration:")
        for key, value in cfg.model_dump().items():
            utils.print_item(key, value)
        utils.print_hint("nx config edit", "edit configuration")
    except Exception as e:
        utils.print_error(f"displaying config: {e}")


def show_env() -> None:
    try:
        env_vars = setup.load_current_env()
        utils.print_header("Current Environment Variables:")
        for key, value in env_vars.items():
            if utils.is_sensitive_key(key):
                value = "********"
            utils.print_item(key, value)
        utils.print_hint("nx env edit", "edit environment variables")
    except Exception as e:
        utils.print_error(f"displaying environment variables: {e}")


def show_jobrc() -> None:
    try:
        jobrc_path = setup.get_jobrc_path()
        if not jobrc_path.exists():
            utils.print_warning("No job runtime configuration file found. Create one with 'nx jobrc edit'")
            return

        utils.print_header("Current Job Runtime Configuration:")
        with open(jobrc_path) as f:
            content = f.read()
            print(content)
        utils.print_hint("nx jobrc edit", "edit job runtime configuration")
    except Exception as e:
        utils.print_error(f"displaying job runtime configuration: {e}")


def show_version() -> None:
    utils.print_item("Nexus CLI version", VERSION)


def add_job_run_parser(subparsers) -> None:
    run_parser = subparsers.add_parser("run", help="Run a job")
    run_parser.add_argument(
        "commands",
        nargs="*",
        help='Command to run, e.g., "python train.py". If not provided, starts an interactive shell.',
    )
    run_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    run_parser.add_argument(
        "-g", "--gpus", type=int, default=1, help="Number of GPUs to use (ignored if --gpu-idxs is specified)"
    )
    run_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    run_parser.add_argument("-f", "--force", action="store_true", help="Ignore GPU blacklist")
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    run_parser.add_argument("--interactive", action="store_true", help="Start an interactive shell session on GPU(s)")


def add_job_management_parsers(subparsers) -> None:
    # Add jobs to queue
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command(s) to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0, help="Set job priority (higher values run first)")
    add_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    add_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    add_parser.add_argument("-g", "--gpus", type=int, default=1, help="Number of GPUs to use for the job")
    add_parser.add_argument("-f", "--force", action="store_true", help="Ignore GPU blacklist")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Show queue
    subparsers.add_parser("queue", help="Show pending jobs (queued)")

    # Kill jobs
    kill_parser = subparsers.add_parser(
        "kill", help="Kill running job(s) by GPU index, job ID, or regex (latest job if no arguments)"
    )
    kill_parser.add_argument(
        "targets",
        nargs="*",
        help="List of GPU indices, job IDs, or command regex patterns (optional, kills latest job if omitted)",
    )
    kill_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Remove jobs
    remove_parser = subparsers.add_parser("remove", help="Remove queued job(s) by ID or regex")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Edit jobs
    edit_parser = subparsers.add_parser("edit", help="Edit a queued job's command, priority or GPU count")
    edit_parser.add_argument("job_id", help="Job ID to edit")
    edit_parser.add_argument("-c", "--command", dest="new_command", help="New command to run")
    edit_parser.add_argument("-p", "--priority", type=int, help="New priority value")
    edit_parser.add_argument("-g", "--gpus", type=int, dest="num_gpus", help="New number of GPUs")
    edit_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")


def add_job_monitoring_parsers(subparsers) -> None:
    # Logs command
    logs_parser = subparsers.add_parser("logs", help="View logs for job")
    logs_parser.add_argument("id", nargs="?", help="Job ID or GPU index (optional, most recent job if omitted)")
    logs_parser.add_argument("-t", "--tail", type=int, metavar="N", help="Show only the last N lines")

    # Attach command
    attach_parser = subparsers.add_parser("attach", help="Attach to a running job's screen session")
    attach_parser.add_argument(
        "id", nargs="?", help="Job ID or GPU index to attach to (optional, last job ran if omitted)"
    )

    # History command
    history_parser = subparsers.add_parser("history", help="Show completed, failed, or killed jobs")
    history_parser.add_argument("pattern", nargs="?", help="Filter jobs by command regex pattern")

    # Get command
    get_parser = subparsers.add_parser("get", help="Get detailed information about a job")
    get_parser.add_argument("job_id", help="Job ID to get information about")

    # Health command
    health_parser = subparsers.add_parser("health", help="Show detailed node health information")
    health_parser.add_argument("-r", "--refresh", action="store_true", help="Force refresh of health metrics")


def add_config_parsers(subparsers) -> None:
    # Config command
    config_parser = subparsers.add_parser("config", help="Display or edit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", help="Config actions")
    config_subparsers.add_parser("edit", help="Edit configuration in editor")

    # Env command
    env_parser = subparsers.add_parser("env", help="Display or edit environment variables")
    env_subparsers = env_parser.add_subparsers(dest="env_action", help="Environment actions")
    env_subparsers.add_parser("edit", help="Edit environment variables in editor")

    env_set_parser = env_subparsers.add_parser("set", help="Set an environment variable")
    env_set_parser.add_argument("key_value", nargs="?", help="KEY=VALUE format or just KEY to be prompted for value")

    env_unset_parser = env_subparsers.add_parser("unset", help="Remove an environment variable")
    env_unset_parser.add_argument("key", help="Environment variable to remove")

    # Jobrc command
    jobrc_parser = subparsers.add_parser("jobrc", help="Manage job runtime configuration (.jobrc)")
    jobrc_subparsers = jobrc_parser.add_subparsers(dest="jobrc_action", help="Jobrc actions")
    jobrc_subparsers.add_parser("edit", help="Edit job runtime configuration in editor")


def add_utility_parsers(subparsers) -> None:
    # GPU blacklist management
    blacklist_parser = subparsers.add_parser("blacklist", help="Manage GPU blacklist")
    blacklist_subparsers = blacklist_parser.add_subparsers(
        dest="blacklist_action", help="Blacklist commands", required=True
    )
    blacklist_add = blacklist_subparsers.add_parser("add", help="Add GPUs to blacklist")
    blacklist_add.add_argument("gpus", help="Comma-separated GPU indices to blacklist (e.g., '0,1,2')")
    blacklist_remove = blacklist_subparsers.add_parser("remove", help="Remove GPUs from blacklist")
    blacklist_remove.add_argument("gpus", help="Comma-separated GPU indices to remove from blacklist")

    # Setup, version, help
    setup_parser = subparsers.add_parser("setup", help="Run setup wizard")
    setup_parser.add_argument(
        "--non-interactive", action="store_true", help="Set up non-interactively using environment variables"
    )
    subparsers.add_parser("version", help="Show version information")
    subparsers.add_parser("help", help="Show help information")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add all command parsers in a modular way
    add_job_run_parser(subparsers)
    add_job_management_parsers(subparsers)
    add_job_monitoring_parsers(subparsers)
    add_config_parsers(subparsers)
    add_utility_parsers(subparsers)

    return parser


def get_command_handlers(args, cfg: NexusCliConfig, parser: argparse.ArgumentParser):
    """Returns a dictionary of command handlers that don't require API connection."""
    return {
        "config": lambda: handle_config(args, cfg),
        "env": lambda: handle_env(args),
        "jobrc": lambda: handle_jobrc(args),
        "setup": lambda: handle_setup(args),
        "version": lambda: show_version(),
        "help": lambda: parser.print_help(),
    }


def get_api_command_handlers(args, cfg: NexusCliConfig):
    """Returns a dictionary of command handlers that require API connection."""
    return {
        "add": lambda: jobs.add_jobs(
            cfg,
            args.commands,
            repeat=args.repeat,
            priority=args.priority,
            gpu_idxs_str=args.gpu_idxs,
            num_gpus=args.gpus,
            notification_types=args.notify,
            force=args.force,
            bypass_confirm=args.yes,
        ),
        "run": lambda: jobs.run_job(
            cfg,
            args.commands,
            gpu_idxs_str=args.gpu_idxs,
            num_gpus=args.gpus,
            notification_types=args.notify,
            force=args.force,
            bypass_confirm=args.yes,
            interactive=not args.commands,
        ),
        "queue": lambda: jobs.show_queue(),
        "history": lambda: jobs.show_history(getattr(args, "pattern", None)),
        "kill": lambda: jobs.kill_jobs(getattr(args, "targets", None), bypass_confirm=args.yes),
        "remove": lambda: jobs.remove_jobs(args.job_ids, bypass_confirm=args.yes),
        "blacklist": lambda: jobs.handle_blacklist(args),
        "logs": lambda: jobs.view_logs(args.id, tail=args.tail),
        "attach": lambda: jobs.attach_to_job(cfg, args.id),
        "health": lambda: jobs.show_health(refresh=args.refresh),
        "get": lambda: jobs.get_job_info(args.job_id),
        "edit": lambda: jobs.edit_job_command(
            args.job_id,
            command=args.new_command,
            priority=args.priority,
            num_gpus=args.num_gpus,
            bypass_confirm=args.yes,
        ),
    }


def dispatch_command(command_name: str, handlers: dict) -> bool:
    """Dispatch command to appropriate handler. Returns True if handled."""
    handler = handlers.get(command_name)
    if handler:
        handler()
        return True
    return False


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    # First-time setup check
    if not setup.check_config_exists() and (not isinstance(args.command, list) and args.command != "setup"):
        utils.print_header("Welcome to Nexus! Running first-time setup wizard...")
        setup.setup_wizard()
        if not hasattr(args, "command") or not args.command:
            jobs.print_status()
            return

    # Load configuration
    cfg = config.load_config()

    # Default to showing status if no command provided
    if not hasattr(args, "command") or not args.command:
        jobs.print_status()
        return

    # Extract command name from args
    command_name = args.command[0] if isinstance(args.command, list) else args.command

    # Try to dispatch to non-API commands first
    no_api_handlers = get_command_handlers(args, cfg, parser)
    if dispatch_command(command_name, no_api_handlers):
        return

    # Check API connection before proceeding with API commands
    if not api_client.check_api_connection():
        utils.print_error("Cannot connect to Nexus API. Ensure the server is running.")
        sys.exit(1)

    # Try to dispatch to API commands
    api_handlers = get_api_command_handlers(args, cfg)
    if not dispatch_command(command_name, api_handlers):
        parser.print_help()


def handle_resource(resource_type: str, args, cfg: NexusCliConfig | None = None) -> None:
    """Generic handler for config, env, and jobrc resources."""
    action_attr = f"{resource_type}_action"
    resource_has_action = hasattr(args, action_attr) and getattr(args, action_attr) is not None

    if not resource_has_action:
        # Show the resource if no action specified
        if resource_type == "config" and cfg is not None:
            show_config(cfg)
        elif resource_type == "env":
            show_env()
        elif resource_type == "jobrc":
            show_jobrc()
        return

    # Handle the action
    action = getattr(args, action_attr)

    if action == "edit":
        if resource_type == "config":
            setup.open_config_editor()
        elif resource_type == "env":
            setup.open_env_editor()
        elif resource_type == "jobrc":
            setup.open_jobrc_editor()
    elif resource_type == "env":
        if action == "set":
            set_env_var(args)
        elif action == "unset":
            unset_env_var(args)


def handle_config(args, cfg: NexusCliConfig) -> None:
    handle_resource("config", args, cfg)


def handle_env(args) -> None:
    handle_resource("env", args)


def parse_env_key_value(key_value: str | None) -> tuple[str, str]:
    """Parse KEY=VALUE format or prompt for value if just KEY is provided."""
    if key_value and "=" in key_value:
        # KEY=VALUE format
        key, value = key_value.split("=", 1)
        return key.strip(), value.strip()

    # Just KEY or empty, prompt for values
    key = key_value.strip() if key_value else ""
    if not key:
        key = utils.get_user_input("Environment variable name", required=True)

    # Get value with masking for sensitive keys
    mask_input = utils.is_sensitive_key(key)
    value = utils.get_user_input(f"Value for {key}", required=True, mask_input=mask_input)
    return key, value


def set_env_var(args) -> None:
    try:
        key, value = parse_env_key_value(args.key_value)
        env_vars = setup.load_current_env()
        env_vars[key] = value
        setup.save_env_vars(env_vars)
        utils.print_success(f"Environment variable '{key}' has been set")
    except Exception as e:
        utils.print_error(f"setting environment variable: {e}")


def unset_env_var(args) -> None:
    try:
        key = args.key
        env_vars = setup.load_current_env()

        if key in env_vars:
            del env_vars[key]
            setup.save_env_vars(env_vars)
            utils.print_success(f"Environment variable '{key}' has been removed")
        else:
            utils.print_warning(f"Environment variable '{key}' does not exist")
    except Exception as e:
        utils.print_error(f"removing environment variable: {e}")


def handle_jobrc(args) -> None:
    handle_resource("jobrc", args)


def handle_setup(args) -> None:
    if args.non_interactive:
        setup.setup_non_interactive()
    else:
        setup.setup_wizard()


if __name__ == "__main__":
    main()
