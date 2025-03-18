import argparse
import importlib.metadata
import sys

from termcolor import colored

from nexus.cli import api_client, config, jobs, setup
from nexus.cli.config import NexusCliConfig

try:
    VERSION = importlib.metadata.version("nexusai")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"


def show_config(cfg: NexusCliConfig) -> None:
    try:
        print(colored("Current Configuration:", "blue", attrs=["bold"]))
        for key, value in cfg.model_dump().items():
            print(f"{colored(key, 'cyan')}: {value}")
        print(f"\nTo edit configuration: {colored('nx config edit', 'green')}")
    except Exception as e:
        print(colored(f"Error displaying config: {e}", "red"))


def show_env() -> None:
    try:
        env_vars = setup.load_current_env()
        print(colored("Current Environment Variables:", "blue", attrs=["bold"]))
        for key, value in env_vars.items():
            if any(sensitive in key.lower() for sensitive in ["key", "token", "secret", "password", "sid", "number"]):
                value = "********"
            print(f"{colored(key, 'cyan')}: {value}")
        print(f"\nTo edit environment variables: {colored('nx nv edit', 'green')}")
    except Exception as e:
        print(colored(f"Error displaying environment variables: {e}", "red"))


def show_jobrc() -> None:
    try:
        jobrc_path = setup.get_jobrc_path()
        if not jobrc_path.exists():
            print(colored("No job runtime configuration file found. Create one with 'nx jobrc edit'", "yellow"))
            return

        print(colored("Current Job Runtime Configuration:", "blue", attrs=["bold"]))
        with open(jobrc_path) as f:
            content = f.read()
            print(content)
        print(f"\nTo edit job runtime configuration: {colored('nx jobrc edit', 'green')}")
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

    # Basic job commands
    run_parser = subparsers.add_parser("run", help="Run a job")
    run_parser.add_argument("commands", nargs="+", help='Command to run, e.g., "python train.py"')
    run_parser.add_argument(
        "-i", "--gpu-idxs", dest="gpu_idxs", help="Specific GPU indices to run on (e.g., '0' or '0,1' for multi-GPU)"
    )
    run_parser.add_argument(
        "-g", "--gpus", type=int, default=1, help="Number of GPUs to use (ignored if --gpu-idxs is specified)"
    )
    run_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command(s) to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0, help="Set job priority (higher values run first)")
    add_parser.add_argument("-n", "--notify", nargs="+", help="Additional notification types for this job")
    add_parser.add_argument("-g", "--gpus", type=int, default=1, help="Number of GPUs to use for the job")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    subparsers.add_parser("queue", help="Show pending jobs (queued)")

    # Job control commands
    kill_parser = subparsers.add_parser("kill", help="Kill running job(s) by GPU index, job ID, or regex")
    kill_parser.add_argument("targets", nargs="+", help="List of GPU indices, job IDs, or command regex patterns")
    kill_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    remove_parser = subparsers.add_parser("remove", help="Remove queued job(s) by ID or regex")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    edit_parser = subparsers.add_parser("edit", help="Edit a queued job's command or priority")
    edit_parser.add_argument("job_id", help="Job ID to edit")
    edit_parser.add_argument("-c", "--command", dest="new_command", help="New command to run")
    edit_parser.add_argument("-p", "--priority", type=int, help="New priority value")
    edit_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Job monitoring commands
    logs_parser = subparsers.add_parser("logs", help="View logs for job")
    logs_parser.add_argument("id", nargs="?", help="Job ID or GPU index (optional, most recent job if omitted)")
    logs_parser.add_argument("-t", "--tail", type=int, metavar="N", help="Show only the last N lines")

    attach_parser = subparsers.add_parser("attach", help="Attach to a running job's screen session")
    attach_parser.add_argument(
        "id", nargs="?", help="Job ID or GPU index to attach to (optional, last job ran if omitted)"
    )

    history_parser = subparsers.add_parser("history", help="Show completed, failed, or killed jobs")
    history_parser.add_argument("pattern", nargs="?", help="Filter jobs by command regex pattern")

    health_parser = subparsers.add_parser("health", help="Show detailed node health information")
    health_parser.add_argument("-r", "--refresh", action="store_true", help="Force refresh of health metrics")

    # ====== CONFIGURATION COMMANDS (SECOND) ======
    # Basic configuration commands
    config_parser = subparsers.add_parser("config", help="Display or edit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", help="Config actions")
    config_subparsers.add_parser("edit", help="Edit configuration in editor")

    env_parser = subparsers.add_parser("env", help="Display or edit environment variables")
    env_subparsers = env_parser.add_subparsers(dest="env_action", help="Environment actions")
    env_subparsers.add_parser("edit", help="Edit environment variables in editor")

    jobrc_parser = subparsers.add_parser("jobrc", help="Manage job runtime configuration (.jobrc)")
    jobrc_subparsers = jobrc_parser.add_subparsers(dest="jobrc_action", help="Jobrc actions")
    jobrc_subparsers.add_parser("edit", help="Edit job runtime configuration in editor")

    # GPU management
    blacklist_parser = subparsers.add_parser("blacklist", help="Manage GPU blacklist")
    blacklist_subparsers = blacklist_parser.add_subparsers(
        dest="blacklist_action", help="Blacklist commands", required=True
    )
    blacklist_add = blacklist_subparsers.add_parser("add", help="Add GPUs to blacklist")
    blacklist_add.add_argument("gpus", help="Comma-separated GPU indices to blacklist (e.g., '0,1,2')")
    blacklist_remove = blacklist_subparsers.add_parser("remove", help="Remove GPUs from blacklist")
    blacklist_remove.add_argument("gpus", help="Comma-separated GPU indices to remove from blacklist")

    # ====== UTILITY COMMANDS (LAST) ======
    setup_parser = subparsers.add_parser("setup", help="Run setup wizard")
    setup_parser.add_argument(
        "--non-interactive", action="store_true", help="Set up non-interactively using environment variables"
    )
    subparsers.add_parser("version", help="Show version information")
    subparsers.add_parser("help", help="Show help information")

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if not setup.check_config_exists() and (not isinstance(args.command, list) and args.command != "setup"):
        print(colored("Welcome to Nexus! Running first-time setup wizard...", "blue"))
        setup.setup_wizard()
        if not hasattr(args, "command") or not args.command:
            jobs.print_status()
            return

    cfg = config.load_config()

    if not hasattr(args, "command") or not args.command:
        jobs.print_status()
        return

    no_api_commands = {
        "config": lambda: handle_config(args, cfg),
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
            cfg,
            args.commands,
            repeat=args.repeat,
            priority=args.priority,
            num_gpus=args.gpus,
            notification_types=args.notify,
            bypass_confirm=args.yes,
        ),
        "run": lambda: jobs.run_job(
            cfg,
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
        "attach": lambda: jobs.attach_to_job(cfg, args.id),
        "health": lambda: jobs.show_health(refresh=args.refresh),
        "edit": lambda: jobs.edit_job_command(
            args.job_id,
            command=args.new_command,
            priority=args.priority,
            bypass_confirm=args.yes,
        ),
    }
    if not isinstance(args.command, str):
        parser.print_help()
    else:
        handler = command_handlers.get(args.command)
        if handler:
            handler()
        else:
            parser.print_help()


def handle_config(args, cfg: NexusCliConfig) -> None:
    if hasattr(args, "config_action") and args.config_action == "edit":
        setup.open_config_editor()
    else:
        show_config(cfg)


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
        cfg = config.load_config()
        config.save_config(cfg)
        print(colored("Configuration initialized from environment variables", "green"))
        print(f"Configuration saved to: {config.get_config_path()}")
    else:
        setup.setup_wizard()


if __name__ == "__main__":
    main()
