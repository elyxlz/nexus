import argparse
import importlib.metadata
import sys

from termcolor import colored

from nexus.cli import api_client, config, jobs, setup

try:
    VERSION = importlib.metadata.version("nexusai")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"


def show_config() -> None:
    try:
        cfg = config.load_config()
        print(colored("Current Configuration:", "blue", attrs=["bold"]))
        for key, value in cfg.model_dump().items():
            print(f"{colored(key, 'cyan')}: {value}")
    except Exception as e:
        print(colored(f"Error displaying config: {e}", "red"))


def show_env() -> None:
    try:
        env_vars = setup.load_current_env()
        print(colored("Current Environment Variables:", "blue", attrs=["bold"]))
        for key, value in env_vars.items():
            # Hide sensitive values like API keys and tokens
            if any(sensitive in key.lower() for sensitive in ["key", "token", "secret", "password", "sid"]):
                value = "********"
            print(f"{colored(key, 'cyan')}: {value}")
    except Exception as e:
        print(colored(f"Error displaying environment variables: {e}", "red"))


def show_jobrc() -> None:
    try:
        jobrc_path = setup.get_jobrc_path()
        if not jobrc_path.exists():
            print(colored("No job runtime configuration file found. Create one with 'nexus jobrc edit'", "yellow"))
            return

        print(colored("Current Job Runtime Configuration:", "blue", attrs=["bold"]))
        with open(jobrc_path) as f:
            content = f.read()
            print(content)
    except Exception as e:
        print(colored(f"Error displaying job runtime configuration: {e}", "red"))


def show_version() -> None:
    print(f"Nexus CLI version: {colored(VERSION, 'cyan')}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("help", help="Show help information")
    subparsers.add_parser("queue", help="Show pending jobs (queued)")

    # Run command for immediate execution
    run_parser = subparsers.add_parser("run", help="Run a job immediately")
    run_parser.add_argument("commands", nargs="+", help='Command to run, e.g., "python train.py"')
    run_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    run_parser.add_argument("-g", "--gpus", type=int, default=1, help="Number of GPUs to use (ignored if --gpu-idxs is specified)")
    # User parameter removed
    run_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command(s) to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0, help="Set job priority (higher values run first)")
    # User parameter removed
    add_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    add_parser.add_argument("-g", "--gpus", type=int, default=1, help="Number of GPUs to use for the job")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Config command with subcommands
    config_parser = subparsers.add_parser("config", help="Display or edit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", help="Config actions")
    config_subparsers.add_parser("edit", help="Edit configuration in editor")

    # Env command with subcommands
    env_parser = subparsers.add_parser("env", help="Display or edit environment variables")
    env_subparsers = env_parser.add_subparsers(dest="env_action", help="Environment actions")
    env_subparsers.add_parser("edit", help="Edit environment variables in editor")

    # Jobrc command with subcommands
    jobrc_parser = subparsers.add_parser("jobrc", help="Manage job runtime configuration (.jobrc)")
    jobrc_subparsers = jobrc_parser.add_subparsers(dest="jobrc_action", help="Jobrc actions")
    jobrc_subparsers.add_parser("edit", help="Edit job runtime configuration in editor")
    setup_parser = subparsers.add_parser("setup", help="Run setup wizard")
    setup_parser.add_argument(
        "--non-interactive", action="store_true", help="Set up non-interactively using environment variables"
    )
    subparsers.add_parser("version", help="Show version information")

    kill_parser = subparsers.add_parser("kill", help="Kill running job(s) by GPU index, job ID, or regex")
    kill_parser.add_argument("targets", nargs="+", help="List of GPU indices, job IDs, or command regex patterns")
    kill_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    remove_parser = subparsers.add_parser("remove", help="Remove queued job(s) by ID or regex")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    history_parser = subparsers.add_parser("history", help="Show completed, failed, or killed jobs")
    history_parser.add_argument("pattern", nargs="?", help="Filter jobs by command regex pattern")

    blacklist_parser = subparsers.add_parser("blacklist", help="Manage GPU blacklist")
    blacklist_subparsers = blacklist_parser.add_subparsers(
        dest="blacklist_action", help="Blacklist commands", required=True
    )

    blacklist_add = blacklist_subparsers.add_parser("add", help="Add GPUs to blacklist")
    blacklist_add.add_argument("gpus", help="Comma-separated GPU indices to blacklist (e.g., '0,1,2')")

    blacklist_remove = blacklist_subparsers.add_parser("remove", help="Remove GPUs from blacklist")
    blacklist_remove.add_argument("gpus", help="Comma-separated GPU indices to remove from blacklist")

    logs_parser = subparsers.add_parser("logs", help="View logs for job")
    logs_parser.add_argument("id", help="Job ID or GPU index")
    logs_parser.add_argument("-t", "--tail", type=int, metavar="N", help="Show only the last N lines")

    # Add attach command
    attach_parser = subparsers.add_parser("attach", help="Attach to a running job's screen session")
    attach_parser.add_argument("id", help="Job ID or GPU index to attach to")

    # Add health command
    subparsers.add_parser("health", help="Show detailed node health information")

    # Add update command
    update_parser = subparsers.add_parser("update", help="Update a queued job's command or priority")
    update_parser.add_argument("job_id", help="Job ID to update")
    update_parser.add_argument("-c", "--command", help="New command to run")
    update_parser.add_argument("-p", "--priority", type=int, help="New priority value")
    update_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()
    
    # No longer needed as we're using the consistent approach for both run and add commands

    # First time setup handling
    if not setup.check_config_exists() and (not isinstance(args.command, list) and args.command != "setup"):
        print(colored("Welcome to Nexus! Running first-time setup wizard...", "blue"))
        setup.setup_wizard()
        if not hasattr(args, 'command') or not args.command:
            jobs.print_status()
            return

    if not hasattr(args, 'command') or not args.command:
        jobs.print_status()
        return

    no_api_commands = {
        "config": lambda: handle_config(args),
        "env": lambda: handle_env(args),
        "jobrc": lambda: handle_jobrc(args),
        "setup": lambda: handle_setup(args),
        "version": lambda: show_version(),
        "help": lambda: parser.print_help(),
    }

    command_name = args.command[0] if isinstance(args.command, list) else args.command
    if command_name in no_api_commands:
        no_api_commands[command_name]()
        return

    if not api_client.check_api_connection():
        print(colored("Error: Cannot connect to Nexus API. Ensure the server is running.", "red"))
        sys.exit(1)

    command_handlers = {
        "add": lambda: jobs.add_jobs(
            args.commands,
            repeat=args.repeat,
            priority=args.priority,
            num_gpus=args.gpus,
            notification_types=args.notify,
            bypass_confirm=args.yes,
        ),
        "run": lambda: jobs.run_job(
            args.commands,
            gpu_idxs_str=args.gpu_idxs,
            num_gpus=args.gpus,
            notification_types=args.notify,
            bypass_confirm=args.yes,
        ),
        "queue": lambda: jobs.show_queue(),
        "history": lambda: jobs.show_history(getattr(args, "pattern", None)),
        "kill": lambda: jobs.kill_jobs(args.targets, bypass_confirm=args.yes),
        "remove": lambda: jobs.remove_jobs(args.job_ids, bypass_confirm=args.yes),
        "blacklist": lambda: jobs.handle_blacklist(args),
        "logs": lambda: jobs.view_logs(args.id, tail=args.tail),
        "attach": lambda: jobs.attach_to_job(args.id),
        "health": lambda: jobs.show_health(),
        "update": lambda: jobs.update_job_command(
            args.job_id,
            command=" ".join(args.command) if hasattr(args, "command") and args.command else None,
            priority=args.priority,
            bypass_confirm=args.yes,
        ),
    }
    handler = command_handlers.get(args.command, parser.print_help)
    handler()


def handle_config(args) -> None:
    if hasattr(args, "config_action") and args.config_action == "edit":
        setup.open_config_editor()
    else:
        show_config()


def handle_env(args) -> None:
    if hasattr(args, "env_action") and args.env_action == "edit":
        setup.open_env_editor()
    else:
        show_env()


def handle_jobrc(args) -> None:
    if hasattr(args, "jobrc_action") and args.jobrc_action == "edit":
        setup.open_jobrc_editor()
    else:
        show_jobrc()


def handle_setup(args) -> None:
    if hasattr(args, "non_interactive") and args.non_interactive:
        # Let Pydantic populate the config from environment variables
        cfg = config.load_config()
        config.save_config(cfg)
        print(colored("Configuration initialized from environment variables", "green"))
        print(f"Configuration saved to: {config.get_config_path()}")
    else:
        setup.setup_wizard()


if __name__ == "__main__":
    main()
