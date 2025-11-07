import argparse
import importlib.metadata
import sys

import argcomplete

from nexus.cli import api_client, config, jobs, setup, shell_completion, utils
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
    run_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    run_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    run_parser.add_argument(
        "-g", "--gpus", type=int, default=1, help="Number of GPUs to use (ignored if --gpu-idxs is specified)"
    )
    run_parser.add_argument("-c", "--cpu", action="store_true", help="Run job on CPU only (no GPUs)")
    run_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    run_parser.add_argument("-s", "--silent", action="store_true", help="Disable all notifications for this job")
    run_parser.add_argument("-f", "--force", action="store_true", help="Ignore GPU blacklist")
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    run_parser.add_argument("-l", "--local", action="store_true", help="Skip git tag creation entirely")
    run_parser.add_argument("--interactive", action="store_true", help="Start an interactive shell session on GPU(s)")
    run_parser.add_argument(
        "commands",
        nargs=argparse.REMAINDER,
        help="Command to run (everything after flags, no quotes needed). If not provided, starts an interactive shell.",
    )


def add_job_management_parsers(subparsers) -> None:
    # Add jobs to queue
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0, help="Set job priority (higher values run first)")
    add_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    add_parser.add_argument("-s", "--silent", action="store_true", help="Disable all notifications for this job")
    add_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    add_parser.add_argument("-g", "--gpus", type=int, default=1, help="Number of GPUs to use for the job")
    add_parser.add_argument("-c", "--cpu", action="store_true", help="Run job on CPU only (no GPUs)")
    add_parser.add_argument("-f", "--force", action="store_true", help="Ignore GPU blacklist")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    add_parser.add_argument("-l", "--local", action="store_true", help="Skip git tag creation entirely")
    add_parser.add_argument(
        "commands", nargs=argparse.REMAINDER, help="Command to add (everything after flags, no quotes needed)"
    )

    # Show queue
    queue_parser = subparsers.add_parser("queue", help="Show pending jobs (queued)")
    queue_parser.add_argument("-t", "--target", help="Target server (name or 'local')")

    # Kill jobs
    kill_parser = subparsers.add_parser(
        "kill", help="Kill running job(s) by GPU index, job ID, or regex (latest job if no arguments)"
    )
    kill_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    kill_parser.add_argument(
        "targets",
        nargs="*",
        help="List of GPU indices, job IDs, or command regex patterns (optional, kills latest job if omitted)",
    )
    kill_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Remove jobs
    remove_parser = subparsers.add_parser("remove", help="Remove queued job(s) by ID or regex")
    remove_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Edit jobs
    edit_parser = subparsers.add_parser("edit", help="Edit a queued job's command, priority or GPU count")
    edit_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    edit_parser.add_argument("job_id", help="Job ID to edit")
    edit_parser.add_argument("-c", "--command", dest="new_command", help="New command to run")
    edit_parser.add_argument("-p", "--priority", type=int, help="New priority value")
    edit_parser.add_argument("-g", "--gpus", type=int, dest="num_gpus", help="New number of GPUs")
    edit_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")


def add_job_monitoring_parsers(subparsers) -> None:
    # Logs command
    logs_parser = subparsers.add_parser("logs", help="View logs for job")
    logs_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    logs_parser.add_argument("id", nargs="?", help="Job ID or GPU index (optional, most recent job if omitted)")
    logs_parser.add_argument("-n", "--tail", type=int, metavar="N", help="Show only the last N lines")

    # Attach command
    attach_parser = subparsers.add_parser("attach", help="Attach to a running job's screen session")
    attach_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    attach_parser.add_argument(
        "id", nargs="?", help="Job ID or GPU index to attach to (optional, last job ran if omitted)"
    )

    # History command
    history_parser = subparsers.add_parser("history", help="Show completed, failed, or killed jobs")
    history_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    history_parser.add_argument("pattern", nargs="?", help="Filter jobs by command regex pattern")

    # Get command
    get_parser = subparsers.add_parser("get", help="Get detailed information about a job")
    get_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
    get_parser.add_argument("job_id", help="Job ID to get information about")

    # Health command
    health_parser = subparsers.add_parser("health", help="Show detailed node health information")
    health_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
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
    blacklist_parser.add_argument("-t", "--target", help="Target server (name or 'local')")
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
    setup_parser.add_argument("--remote", action="store_true", help="Set up connection to a remote Nexus server")

    target_parser = subparsers.add_parser("target", help="Manage target servers")
    target_subs = target_parser.add_subparsers(dest="target_command", required=True)

    target_subs.add_parser("add", help="Add target server")
    target_subs.add_parser("list", help="List all targets")

    default_p = target_subs.add_parser("default", help="Set default target")
    default_p.add_argument("name", help="Target name or 'local'")

    remove_p = target_subs.add_parser("remove", help="Remove a target")
    remove_p.add_argument("name", help="Target name to remove")

    subparsers.add_parser("version", help="Show version information")
    subparsers.add_parser("help", help="Show help information")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("-t", "--target", help="Target server (name or 'local')")

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
        "target": lambda: handle_target(args),
        "version": lambda: show_version(),
        "help": lambda: parser.print_help(),
    }


def get_api_command_handlers(args, cfg: NexusCliConfig):
    """Returns a dictionary of command handlers that require API connection."""
    target_name = getattr(args, "target", None)
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
            silent=args.silent,
            local=args.local,
            target_name=target_name,
            cpu=args.cpu,
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
            silent=args.silent,
            local=args.local,
            target_name=target_name,
            cpu=args.cpu,
        ),
        "queue": lambda: jobs.show_queue(target_name=target_name),
        "history": lambda: jobs.show_history(getattr(args, "pattern", None), target_name=target_name),
        "kill": lambda: jobs.kill_jobs(
            getattr(args, "targets", None), bypass_confirm=args.yes, target_name=target_name
        ),
        "remove": lambda: jobs.remove_jobs(args.job_ids, bypass_confirm=args.yes, target_name=target_name),
        "blacklist": lambda: jobs.handle_blacklist(args, target_name=target_name),
        "logs": lambda: jobs.view_logs(cfg, args.id, tail=args.tail, target_name=target_name),
        "attach": lambda: jobs.attach_to_job(cfg, args.id, target_name=target_name),
        "health": lambda: jobs.show_health(refresh=args.refresh, target_name=target_name),
        "get": lambda: jobs.get_job_info(args.job_id, target_name=target_name),
        "edit": lambda: jobs.edit_job_command(
            args.job_id,
            command=args.new_command,
            priority=args.priority,
            num_gpus=args.num_gpus,
            bypass_confirm=args.yes,
            target_name=target_name,
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

    argcomplete.autocomplete(parser)

    shell_completion.check_and_prompt_completion()

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
        target_name = getattr(args, "target", None)
        jobs.print_status(target_name=target_name)
        return

    # Extract command name from args
    command_name = args.command[0] if isinstance(args.command, list) else args.command

    # Try to dispatch to non-API commands first
    no_api_handlers = get_command_handlers(args, cfg, parser)
    if dispatch_command(command_name, no_api_handlers):
        return

    # Check API connection before proceeding with API commands
    target_name = getattr(args, "target", None)
    if not api_client.check_api_connection(target_name):
        active_target_name, target_cfg = config.get_active_target(target_name)
        if target_cfg:
            error_msg = f"Cannot connect to target '{active_target_name}' ({target_cfg.host}:{target_cfg.port})"
        else:
            error_msg = "Cannot connect to local server (localhost:54323)"
        utils.print_error(error_msg)
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
    if args.remote:
        setup.add_target()
    elif args.non_interactive:
        setup.setup_non_interactive()
    else:
        setup.setup_wizard()


def handle_target(args) -> None:
    if args.target_command == "add":
        setup.add_target()
    elif args.target_command == "list":
        setup.list_targets()
    elif args.target_command == "default":
        setup.set_default_target(args.name)
    elif args.target_command == "remove":
        setup.remove_target(args.name)


if __name__ == "__main__":
    main()
