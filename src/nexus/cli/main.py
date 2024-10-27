import argparse
import itertools
import os
import pathlib
import re
import subprocess
import sys
import time
import typing

import importlib.metadata

import requests
from termcolor import colored

try:
    VERSION = importlib.metadata.version("nexusai")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"

# Configuration
DEFAULT_CONFIG_PATH = pathlib.Path.home() / ".nexus" / "config.toml"


def load_config(config_path: pathlib.Path) -> dict:
    """Load configuration from config.toml."""
    if not config_path.exists():
        print(colored(f"Configuration file not found at {config_path}.", "red"))
        sys.exit(1)

    import toml

    try:
        config = toml.load(config_path)
        return config
    except toml.TomlDecodeError as e:
        print(colored(f"Error parsing config.toml: {e}", "red"))
        sys.exit(1)


def get_api_base_url():
    """Get API base URL from config. Should only be called after service is started."""
    config = load_config(DEFAULT_CONFIG_PATH)
    return f"http://{config['host']}:{config['port']}/v1"


# Define allowed colors as typing.Literal types
Color = typing.Literal[
    "grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"
]

# Define allowed attributes as typing.Literal types
Attribute = typing.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]


def colored_text(text: str, color: Color, attrs: list[Attribute] | None = None) -> str:
    """Return colored text with optional attributes."""
    return colored(text, color, attrs=attrs)


def is_service_running() -> bool:
    try:
        result = subprocess.run(
            ["screen", "-ls"], capture_output=True, text=True, check=False
        )

        if result.returncode != 0:
            return False

        return any(
            line.strip().split("\t")[0].endswith(".nexus")
            for line in result.stdout.splitlines()
            if "\t" in line and not line.startswith("No Sockets")
        )

    except (subprocess.SubprocessError, OSError, Exception):
        return False


def start_service() -> None:
    """Start the Nexus service in a screen session if it doesn't already exist."""
    if not is_service_running():
        try:
            subprocess.run(
                ["screen", "-S", "nexus", "-dm", "nexus-service"], check=True
            )
            time.sleep(1)
            if not is_service_running():
                raise RuntimeError("Service failed to start")
            print(colored("Nexus service started successfully.", "green"))

        except subprocess.CalledProcessError as e:
            print(colored(f"Error starting Nexus service: {e}", "red"))
            print(
                colored(
                    "Make sure 'screen' and 'nexus-service' are installed and in your PATH.",
                    "yellow",
                )
            )
        except RuntimeError as e:
            print(colored(f"Error: {e}", "red"))
            print(colored("Check the service logs for more information.", "yellow"))
    else:
        return
        # print(colored("Nexus service is already running in a screen session.", "green"))


def print_status_snapshot() -> None:
    """Show status snapshot."""
    try:
        # Ensure the service is running
        assert is_service_running(), "nexus service is not running"

        # Fetch status from the API
        response = requests.get(f"{get_api_base_url()}/service/status")
        response.raise_for_status()
        status = response.json()

        queued = status.get("queued_jobs", 0)
        is_paused = status.get("is_paused", False)

        queue_status = (
            colored_text("PAUSED", "yellow")
            if is_paused
            else colored_text("RUNNING", "green")
        )
        print(
            f"{colored_text('Queue', 'blue')}: {queued} jobs pending [{queue_status}]"
        )
        print(
            f"{colored_text('History', 'blue')}: {status.get('completed_jobs', 0)} jobs completed\n"
        )

        # Fetch GPU status
        gpus_response = requests.get(f"{get_api_base_url()}/gpus")
        gpus_response.raise_for_status()
        gpus = gpus_response.json()

        print(f"{colored_text('GPUs', 'white')}:")
        for gpu in gpus:
            gpu_info = f"GPU {gpu['index']} ({gpu['name']}, {gpu['memory_total']}MB): "

            # Add blacklist status
            if gpu.get("is_blacklisted"):
                gpu_info += colored_text("[BLACKLISTED] ", "red", attrs=["bold"])

            if gpu.get("running_job_id"):
                job_id = gpu["running_job_id"]
                job_response = requests.get(f"{get_api_base_url()}/jobs/{job_id}")
                job_response.raise_for_status()
                job_details = job_response.json()

                runtime = calculate_runtime(job_details)

                job_id_colored = colored_text(job_id, "magenta")
                command = colored_text(
                    job_details.get("command", "N/A"), "white", attrs=["bold"]
                )
                runtime_str = colored_text(format_runtime(runtime), "cyan")
                start_time = colored_text(
                    format_timestamp(job_details.get("started_at")), "cyan"
                )
                gpu_info += (
                    f"{job_id_colored}\n"
                    f"  {colored_text('Command', 'white')}: {command}\n"
                    f"  {colored_text('Runtime', 'cyan')}: {runtime_str}\n"
                    f"  {colored_text('Started', 'cyan')}: {start_time}"
                )
            else:
                gpu_info += colored_text("Available", "green", attrs=["bold"])
            print(gpu_info)
    except requests.RequestException as e:
        print(colored(f"Error fetching status: {e}", "red"))


def format_runtime(seconds: float) -> str:
    """Format runtime in seconds to h m s."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def calculate_runtime(job: dict) -> float:
    """Calculate runtime from job timestamps."""
    if not job.get("started_at"):
        return 0.0

    if job.get("status") == "completed" and job.get("completed_at"):
        return job["completed_at"] - job["started_at"]
    elif job.get("status") == "running":
        return time.time() - job["started_at"]

    return 0.0


def format_timestamp(timestamp: float | None) -> str:
    """Format timestamp to human-readable string including date."""
    if not timestamp:
        return "Unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def stop_service() -> None:
    """Stop the Nexus service."""
    try:
        response = requests.post(f"{get_api_base_url()}/service/stop")
        response.raise_for_status()
        print(colored("Nexus service stopped.", "green"))
    except requests.RequestException as e:
        print(colored(f"Error stopping service: {e}", "red"))


def restart_service() -> None:
    """Restart the Nexus service."""
    try:
        stop_service()
        time.sleep(2)  # Wait for service to stop
        start_service()
    except subprocess.CalledProcessError as e:
        print(colored(f"Error restarting service: {e}", "red"))


def add_jobs(commands: list[str], repeat: int = 1) -> None:
    """Add job(s) to the queue."""
    expanded_commands = []
    for command in commands:
        # Handle repeated commands
        if "-r" in command:
            parts = command.split("-r")
            cmd = parts[0].strip('"').strip()
            try:
                count = int(parts[1].strip())
                expanded_commands.extend([cmd] * count)
            except (IndexError, ValueError):
                print(colored("Invalid repetition format. Use -r <count>.", "red"))
                return
        # Handle parameter combinations
        elif "{" in command and "}" in command:
            param_str = re.findall(r"\{([^}]+)\}", command)
            if not param_str:
                expanded_commands.append(command)
                continue
            params = [p.split(",") for p in param_str]
            for combo in itertools.product(*params):
                temp_cmd = command
                for value in combo:
                    temp_cmd = re.sub(r"\{[^}]+\}", value, temp_cmd, count=1)
                expanded_commands.append(temp_cmd)
        # Handle batched commands
        elif "|" in command:
            batched = command.split("|")
            expanded_commands.extend([cmd.strip() for cmd in batched])
        else:
            expanded_commands.append(command)
    # Repeat commands if needed
    if repeat > 1:
        expanded_commands = expanded_commands * repeat

    # Send to API
    try:
        payload = {
            "commands": expanded_commands,
            "working_dir": os.getcwd(),
        }
        response = requests.post(f"{get_api_base_url()}/jobs", json=payload)
        response.raise_for_status()
        jobs = response.json()
        for job in jobs:
            print(
                f"Added job {colored_text(job['id'], 'magenta', attrs=['bold'])}: {colored_text(job['command'], 'cyan')}"
            )
        # Add summary of total jobs added
        print(
            colored_text(
                f"\nAdded {len(jobs)} jobs to the queue", "green", attrs=["bold"]
            )
        )
    except requests.RequestException as e:
        print(colored(f"Error adding jobs: {e}", "red"))


def show_queue() -> None:
    """Show pending jobs in reverse order, starting from the last job."""
    try:
        response = requests.get(
            f"{get_api_base_url()}/jobs", params={"status": "queued"}
        )
        response.raise_for_status()
        jobs = response.json()
        if not jobs:
            print(colored("No pending jobs.", "green"))
            return

        print(colored("Pending Jobs:", "blue", attrs=["bold"]))
        total_jobs = len(jobs)
        for idx, job in enumerate(reversed(jobs), 1):
            created_time = colored_text(format_timestamp(job.get("created_at")), "cyan")
            print(
                f"{total_jobs - idx + 1}. {colored_text(job['id'], 'magenta')} - "
                f"{colored_text(job['command'], 'white')} "
                f"(Added: {created_time})"
            )

        # Add summary line at the bottom
        print(
            f"\n{colored_text('Total queued jobs:', 'blue', attrs=['bold'])} {colored_text(str(total_jobs), 'cyan')}"
        )

    except requests.RequestException as e:
        print(colored(f"Error fetching queue: {e}", "red"))


def show_history() -> None:
    """Show last 25 completed jobs"""
    try:
        response = requests.get(
            f"{get_api_base_url()}/jobs", params={"status": "completed"}
        )
        response.raise_for_status()
        jobs = response.json()
        if not jobs:
            print(colored("No completed jobs.", "green"))
            return

        for job in jobs[-25:]:
            runtime = calculate_runtime(job)
            gpu = job.get("gpu_index", "Unknown")
            started_time = colored_text(format_timestamp(job.get("started_at")), "cyan")
            print(
                f"{colored_text(job['id'], 'magenta')}: "
                f"{colored_text(job['command'], 'white')} "
                f"(Started: {started_time}, "
                f"Runtime: {colored_text(format_runtime(runtime), 'cyan')}, "
                f"GPU: {colored_text(str(gpu), 'yellow')})"
            )

        # Add total completed jobs count at the bottom
        total_jobs = len(jobs)
        if total_jobs > 25:
            print(
                f"\n{colored_text('Showing last 25 of', 'blue', attrs=['bold'])} {colored_text(str(total_jobs), 'cyan')} {colored_text('total completed jobs', 'blue', attrs=['bold'])}"
            )

    except requests.RequestException as e:
        print(colored(f"Error fetching history: {e}", "red"))


def kill_jobs(pattern: str) -> None:
    """Kill job(s) by ID, GPU number, or command regex."""
    try:
        # Determine if pattern is GPU index
        if pattern.isdigit():
            gpu_index = int(pattern)
            response = requests.post(
                f"{get_api_base_url()}/jobs/kill", json={"gpu_index": gpu_index}
            )
            response.raise_for_status()
            result = response.json()
            killed = result.get("killed", [])
            failed = result.get("failed", [])
            for job_id in killed:
                print(colored(f"Killed job {job_id}", "green"))
            for fail in failed:
                print(
                    colored(f"Failed to kill job {fail['id']}: {fail['error']}", "red")
                )
        else:
            # Assume pattern is job ID or regex
            response = requests.get(
                f"{get_api_base_url()}/jobs", params={"status": "running"}
            )
            response.raise_for_status()
            jobs = response.json()
            matched_jobs = []
            try:
                regex = re.compile(pattern)
            except re.error as e:
                print(colored(f"Invalid regex pattern: {e}", "red"))
                return
            for job in jobs:
                if job["id"] == pattern or regex.search(job["command"]):
                    matched_jobs.append(job["id"])

            if not matched_jobs:
                print(colored("No matching running jobs found.", "yellow"))
                return

            response = requests.post(
                f"{get_api_base_url()}/jobs/kill", json={"job_ids": matched_jobs}
            )
            response.raise_for_status()
            result = response.json()
            killed = result.get("killed", [])
            failed = result.get("failed", [])
            for job_id in killed:
                print(colored(f"Killed job {job_id}", "green"))
            for fail in failed:
                print(
                    colored(f"Failed to kill job {fail['id']}: {fail['error']}", "red")
                )
    except requests.RequestException as e:
        print(colored(f"Error killing jobs: {e}", "red"))


def remove_jobs(pattern: str) -> None:
    """Remove job(s) from queue by ID or command regex."""
    try:
        response = requests.get(
            f"{get_api_base_url()}/jobs", params={"status": "queued"}
        )
        response.raise_for_status()
        jobs = response.json()
        matched_jobs = []
        try:
            regex = re.compile(pattern)
        except re.error as e:
            print(colored(f"Invalid regex pattern: {e}", "red"))
            return
        for job in jobs:
            if job["id"] == pattern or regex.search(job["command"]):
                matched_jobs.append(job["id"])

        if not matched_jobs:
            print(colored("No matching queued jobs found.", "yellow"))
            return

        response = requests.delete(
            f"{get_api_base_url()}/jobs/queued", json={"job_ids": matched_jobs}
        )
        response.raise_for_status()
        result = response.json()
        removed = result.get("removed", [])
        failed = result.get("failed", [])
        for job_id in removed:
            print(colored(f"Removed job {job_id}", "green"))
        for fail in failed:
            print(colored(f"Failed to remove job {fail['id']}: {fail['error']}", "red"))
    except requests.RequestException as e:
        print(colored(f"Error removing jobs: {e}", "red"))


def pause_queue() -> None:
    """Pause queue processing."""
    try:
        response = requests.post(f"{get_api_base_url()}/service/pause")
        response.raise_for_status()
        print(colored("Queue processing paused.", "yellow"))
    except requests.RequestException as e:
        print(colored(f"Error pausing queue: {e}", "red"))


def resume_queue() -> None:
    """Resume queue processing."""
    try:
        response = requests.post(f"{get_api_base_url()}/service/resume")
        response.raise_for_status()
        print(colored("Queue processing resumed.", "green"))
    except requests.RequestException as e:
        print(colored(f"Error resuming queue: {e}", "red"))


def view_logs(job_id: str | None) -> None:
    """View logs for a job or service."""
    try:
        if job_id == "service":
            response = requests.get(f"{get_api_base_url()}/service/logs")
            response.raise_for_status()
            logs = response.json().get("logs", "")
            print(colored("=== Service Logs ===", "blue", attrs=["bold"]))
            print(logs)
        else:
            response = requests.get(f"{get_api_base_url()}/jobs/{job_id}/logs")
            response.raise_for_status()
            logs = response.json()
            stdout = logs.get("stdout", "")
            stderr = logs.get("stderr", "")
            print(colored("=== STDOUT ===", "blue", attrs=["bold"]))
            print(stdout)
            print(colored("\n=== STDERR ===", "red", attrs=["bold"]))
            print(stderr)
    except requests.RequestException as e:
        print(colored(f"Error fetching logs: {e}", "red"))


def attach_to_session(target: str) -> None:
    """Attach to running job's screen session or service."""
    try:
        if target == "service":
            session_name = "nexus"
        elif target.isdigit():
            # Query the GPU status to get the job ID
            response = requests.get(f"{get_api_base_url()}/gpus")
            response.raise_for_status()
            gpus = response.json()

            gpu_index = int(target)
            matching_gpu = next(
                (gpu for gpu in gpus if gpu["index"] == gpu_index), None
            )

            if not matching_gpu:
                print(colored(f"No GPU found with index {gpu_index}", "red"))
                return

            job_id = matching_gpu.get("running_job_id")
            if not job_id:
                print(colored(f"No running job found on GPU {gpu_index}", "yellow"))
                return

            session_name = f"nexus_job_{job_id}"
        else:
            session_name = f"nexus_job_{target}"

        try:
            # Check if the session exists
            result = subprocess.run(
                ["screen", "-ls"], capture_output=True, text=True, check=True
            )

            # Verify the session exists before attempting to attach
            if session_name not in result.stdout:
                print(
                    colored(
                        f"No running screen session found for {session_name}", "red"
                    )
                )
                return

            # Attach to the session
            subprocess.run(["screen", "-r", session_name], check=True)
        except subprocess.CalledProcessError:
            print(colored(f"Error accessing screen session for {target}", "red"))

    except requests.RequestException as e:
        print(colored(f"Error querying GPU status: {e}", "red"))


def view_config() -> None:
    """View current configuration."""
    try:
        with open(DEFAULT_CONFIG_PATH, "r") as f:
            config_content = f.read()
        print(colored("Current Configuration:", "blue", attrs=["bold"]))
        print(config_content)
    except FileNotFoundError:
        print(colored("Configuration file not found.", "red"))


def edit_config() -> None:
    """Edit configuration in $EDITOR."""
    editor = os.environ.get("EDITOR", "vim")
    try:
        subprocess.run([editor, str(DEFAULT_CONFIG_PATH)], check=True)
    except subprocess.CalledProcessError as e:
        print(colored(f"Error editing config: {e}", "red"))


def show_help(command: str | None) -> None:
    """Show help for a specific command or general help."""
    parser.print_help()


def show_version() -> None:
    """Display the version of Nexus CLI."""
    try:
        # Get service version from API
        response = requests.get(f"{get_api_base_url()}/service/version")
        response.raise_for_status()
        service_version = response.json().get("version", "unknown")

        print(f"Nexus CLI version: {colored_text(VERSION, 'cyan', attrs=['bold'])}")
        print(
            f"Nexus service version: {colored_text(service_version, 'cyan', attrs=['bold'])}"
        )
    except requests.RequestException:
        # If service is not running, only show CLI version
        print(f"Nexus CLI version: {colored_text(VERSION, 'cyan', attrs=['bold'])}")
        print(
            colored_text(
                "Note: Could not fetch service version (service may not be running)",
                "yellow",
            )
        )


def parse_gpu_list(gpu_str: str) -> list[int]:
    """Parse a comma-separated string of GPU indexes into a list of integers."""
    try:
        return [int(idx.strip()) for idx in gpu_str.split(",")]
    except ValueError:
        raise ValueError("GPU indexes must be comma-separated numbers (e.g., '0,1,2')")


def blacklist_add(gpu_str: str) -> None:
    """Add GPUs to the blacklist."""
    try:
        # Parse GPU indexes
        try:
            gpu_indexes = parse_gpu_list(gpu_str)
        except ValueError as e:
            print(colored(str(e), "red"))
            return

        # Get current GPU list to validate indexes
        response = requests.get(f"{get_api_base_url()}/gpus")
        response.raise_for_status()
        gpus = response.json()

        # Validate all GPU indexes exist
        valid_indexes = {gpu["index"] for gpu in gpus}
        invalid_indexes = [idx for idx in gpu_indexes if idx not in valid_indexes]
        if invalid_indexes:
            print(
                colored(
                    f"Invalid GPU indexes: {', '.join(map(str, invalid_indexes))}",
                    "red",
                )
            )
            return

        # Send blacklist request
        response = requests.post(
            f"{get_api_base_url()}/gpus/blacklist", json=gpu_indexes
        )
        response.raise_for_status()
        result = response.json()

        blacklisted = result.get("blacklisted", [])
        failed = result.get("failed", [])

        if blacklisted:
            print(
                colored(
                    f"Successfully blacklisted GPUs: {', '.join(map(str, blacklisted))}",
                    "green",
                )
            )

            # Check for running jobs on blacklisted GPUs
            running_jobs = [
                gpu["index"]
                for gpu in gpus
                if gpu["index"] in blacklisted and gpu.get("running_job_id")
            ]
            if running_jobs:
                print(
                    colored(
                        f"Note: GPUs {', '.join(map(str, running_jobs))} have running jobs that will continue, "
                        "but no new jobs will be scheduled.",
                        "yellow",
                    )
                )

        if failed:
            print(colored("Failed to blacklist some GPUs:", "red"))
            for fail in failed:
                print(colored(f"  GPU {fail['index']}: {fail['error']}", "red"))

    except requests.RequestException as e:
        print(colored(f"Error blacklisting GPUs: {e}", "red"))


def blacklist_remove(gpu_str: str) -> None:
    """Remove GPUs from the blacklist."""
    try:
        # Parse GPU indexes
        try:
            gpu_indexes = parse_gpu_list(gpu_str)
        except ValueError as e:
            print(colored(str(e), "red"))
            return

        # Get current GPU list to validate indexes
        response = requests.get(f"{get_api_base_url()}/gpus")
        response.raise_for_status()
        gpus = response.json()

        # Validate all GPU indexes exist
        valid_indexes = {gpu["index"] for gpu in gpus}
        invalid_indexes = [idx for idx in gpu_indexes if idx not in valid_indexes]
        if invalid_indexes:
            print(
                colored(
                    f"Invalid GPU indexes: {', '.join(map(str, invalid_indexes))}",
                    "red",
                )
            )
            return

        # Send unblacklist request
        response = requests.delete(
            f"{get_api_base_url()}/gpus/blacklist", json=gpu_indexes
        )
        response.raise_for_status()
        result = response.json()

        removed = result.get("removed", [])
        failed = result.get("failed", [])

        if removed:
            print(
                colored(
                    f"Successfully removed GPUs from blacklist: {', '.join(map(str, removed))}",
                    "green",
                )
            )

        if failed:
            print(colored("Failed to remove some GPUs from blacklist:", "red"))
            for fail in failed:
                print(colored(f"  GPU {fail['index']}: {fail['error']}", "red"))

    except requests.RequestException as e:
        print(colored(f"Error removing GPUs from blacklist: {e}", "red"))


def handle_blacklist(args):
    """Handle both blacklist add and remove operations"""
    if args.blacklist_action == "add":
        blacklist_add(args.gpus)
    elif args.blacklist_action == "remove":
        blacklist_remove(args.gpus)
    else:
        print(colored("Error: Please specify 'add' or 'remove' action.", "red"))


def main():
    global parser
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # nexus status
    subparsers.add_parser("status", help="Show status snapshot")

    # nexus stop
    subparsers.add_parser("stop", help="Stop the Nexus service")

    # nexus restart
    subparsers.add_parser("restart", help="Restart the Nexus service")

    # nexus add "command"
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument(
        "commands", nargs="+", help='Command to add, e.g., "python train.py"'
    )
    add_parser.add_argument(
        "-r",
        "--repeat",
        type=int,
        default=1,
        help="Repeat the command multiple times",
    )

    # nexus queue
    subparsers.add_parser("queue", help="Show pending jobs")

    # nexus history
    subparsers.add_parser("history", help="Show completed jobs")

    # nexus kill <pattern>
    kill_parser = subparsers.add_parser(
        "kill", help="Kill job(s) by ID, GPU number, or command regex"
    )
    kill_parser.add_argument("pattern", help="Job ID, GPU number, or command regex")

    # nexus remove <pattern>
    remove_parser = subparsers.add_parser(
        "remove", help="Remove job(s) from queue by ID or command regex"
    )
    remove_parser.add_argument("pattern", help="Job ID or command regex")

    # nexus pause
    subparsers.add_parser("pause", help="Pause queue processing")

    # nexus resume
    subparsers.add_parser("resume", help="Resume queue processing")

    # nexus blacklist <gpu1,gpu2>
    blacklist_parser = subparsers.add_parser("blacklist", help="Manage GPU blacklist")

    # Add subcommands to blacklist
    blacklist_subparsers = blacklist_parser.add_subparsers(
        dest="blacklist_action", help="Blacklist commands", required=True
    )

    # Add blacklist add parser
    blacklist_add_parser = blacklist_subparsers.add_parser(
        "add", help="Add GPUs to blacklist"
    )
    blacklist_add_parser.add_argument(
        "gpus",
        help="Comma-separated GPU indexes to blacklist (e.g., '0,1,2')",
    )

    # Add blacklist remove parser
    blacklist_remove_parser = blacklist_subparsers.add_parser(
        "remove", help="Remove GPUs from blacklist"
    )
    blacklist_remove_parser.add_argument(
        "gpus",
        help="Comma-separated GPU indexes to remove from blacklist (e.g., '0,1,2')",
    )
    # nexus logs <id>
    logs_parser = subparsers.add_parser(
        "logs", help="View logs for job (running or completed)"
    )
    logs_parser.add_argument("id", help="Job ID or 'service' to view service logs")

    # nexus attach <id|gpu>
    attach_parser = subparsers.add_parser(
        "attach", help="Attach to running job's screen session"
    )
    attach_parser.add_argument("target", help="Job ID, GPU number, or 'service'")

    # nexus config
    config_parser = subparsers.add_parser("config", help="View or edit current config")
    config_parser.add_argument(
        "action",
        nargs="?",
        choices=["edit"],
        help="Edit configuration in $EDITOR",
    )

    # nexus help
    help_parser = subparsers.add_parser("help", help="Show help")
    help_parser.add_argument("command", nargs="?", help="Command to show detailed help")

    args = parser.parse_args()

    if args.command is None:
        # Default behavior when no command is provided
        start_service()  # Start service if not already running
        print_status_snapshot()  # Show status
        return

    command_handlers = {
        "status": lambda: print_status_snapshot(),
        "stop": lambda: stop_service(),
        "restart": lambda: restart_service(),
        "add": lambda: add_jobs(args.commands, repeat=args.repeat),
        "queue": lambda: show_queue(),
        "history": lambda: show_history(),
        "kill": lambda: kill_jobs(args.pattern),
        "remove": lambda: remove_jobs(args.pattern),
        "pause": lambda: pause_queue(),
        "resume": lambda: resume_queue(),
        "blacklist": lambda: handle_blacklist(args),
        "logs": lambda: view_logs(args.id),
        "attach": lambda: attach_to_session(args.target),
        "config": lambda: edit_config() if args.action == "edit" else view_config(),
        "help": lambda: show_help(args.command),
        "version": lambda: show_version(),
    }
    handler = command_handlers.get(args.command, parser.print_help)
    handler()
