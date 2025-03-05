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

    # Config command with subcommands
    config_parser = subparsers.add_parser("config", help="Display or edit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", help="Config actions")
    config_subparsers.add_parser("edit", help="Edit configuration in editor")

    # Env command with subcommands
    env_parser = subparsers.add_parser("env", help="Display or edit environment variables")
    env_subparsers = env_parser.add_subparsers(dest="env_action", help="Environment actions")
    env_subparsers.add_parser("edit", help="Edit environment variables in editor")
    setup_parser = subparsers.add_parser("setup", help="Run setup wizard")
    setup_parser.add_argument(
        "--non-interactive", action="store_true", help="Set up non-interactively using environment variables"
    )
    subparsers.add_parser("version", help="Show version information")

    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command(s) to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0, help="Set job priority (higher values run first)")
    add_parser.add_argument("-u", "--user", help="Override default username")
    add_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

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

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    # First time setup handling
    if not setup.check_config_exists() and args.command != "setup":
        print(colored("Welcome to Nexus! Running first-time setup wizard...", "blue"))
        setup.setup_wizard()
        if not args.command:
            jobs.print_status()
            return

    if not args.command:
        jobs.print_status()
        return

    no_api_commands = {
        "config": lambda: handle_config(args),
        "env": lambda: handle_env(args),
        "setup": lambda: handle_setup(args),
        "version": lambda: show_version(),
        "help": lambda: parser.print_help(),
    }

    if args.command in no_api_commands:
        no_api_commands[args.command]()
        return

    if not api_client.check_api_connection():
        print(colored("Error: Cannot connect to Nexus API. Ensure the server is running.", "red"))
        sys.exit(1)

    command_handlers = {
        "add": lambda: jobs.add_jobs(
            args.commands,
            repeat=args.repeat,
            user=args.user,
            priority=args.priority,
            notification_types=args.notify,
            bypass_confirm=args.yes,
        ),
        "queue": lambda: jobs.show_queue(),
        "history": lambda: jobs.show_history(getattr(args, "pattern", None)),
        "kill": lambda: jobs.kill_jobs(args.targets, bypass_confirm=args.yes),
        "remove": lambda: jobs.remove_jobs(args.job_ids, bypass_confirm=args.yes),
        "blacklist": lambda: jobs.handle_blacklist(args),
        "logs": lambda: jobs.view_logs(args.id),
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
