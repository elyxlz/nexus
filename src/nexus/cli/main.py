import argparse
import importlib.metadata
import itertools
import os
import pathlib
import re
import subprocess
import sys
import time
import typing

import requests
from termcolor import colored

# Types
Color = typing.Literal["grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
Attribute = typing.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]

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
        return toml.load(config_path)
    except toml.TomlDecodeError as e:
        print(colored(f"Error parsing config.toml: {e}", "red"))
        sys.exit(1)


def get_api_base_url() -> str:
    """Get API base URL from config."""
    config = load_config(DEFAULT_CONFIG_PATH)
    return f"http://{config['host']}:{config['port']}/v1"


# Service Management
def is_service_running() -> bool:
    """Check if the Nexus service is running."""
    try:
        result = subprocess.run(["screen", "-ls"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return False
        return any(
            line.strip().split("\t")[0].endswith(".nexus")
            for line in result.stdout.splitlines()
            if "\t" in line and not line.startswith("No Sockets")
        )
    except (subprocess.SubprocessError, OSError):
        return False


def start_service() -> None:
    """Start the Nexus service if not running."""
    if is_service_running():
        return

    try:
        subprocess.run(["screen", "-S", "nexus", "-dm", "nexus-service"], check=True)
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


# Time Utilities
def format_runtime(seconds: float) -> str:
    """Format runtime in seconds to h m s."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def format_timestamp(timestamp: float | None) -> str:
    """Format timestamp to human-readable string."""
    if not timestamp:
        return "Unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def calculate_runtime(job: dict) -> float:
    """Calculate runtime from job timestamps."""
    if not job.get("started_at"):
        return 0.0
    if job.get("status") == "completed" and job.get("completed_at"):
        return job["completed_at"] - job["started_at"]
    elif job.get("status") == "running":
        return time.time() - job["started_at"]
    return 0.0


# Job Management
def parse_gpu_list(gpu_str: str) -> list[int]:
    """Parse comma-separated GPU indices."""
    try:
        return [int(idx.strip()) for idx in gpu_str.split(",")]
    except ValueError:
        raise ValueError("GPU indexes must be comma-separated numbers (e.g., '0,1,2')")


def parse_targets(targets: list[str]) -> tuple[list[int], list[str]]:
    """Parse targets into GPU indices and job IDs."""
    gpu_indices = []
    job_ids = []

    expanded_targets = []
    for target in targets:
        if "," in target:
            expanded_targets.extend(target.split(","))
        else:
            expanded_targets.append(target)

    for target in expanded_targets:
        if target.strip().isdigit():
            gpu_indices.append(int(target.strip()))
        else:
            job_ids.append(target.strip())

    return gpu_indices, job_ids


def expand_job_commands(commands: list[str], repeat: int = 1) -> list[str]:
    """Expand job commands with repetition and parameter combinations."""
    expanded_commands = []
    for command in commands:
        if "-r" in command:
            parts = command.split("-r")
            cmd = parts[0].strip('"').strip()
            try:
                count = int(parts[1].strip())
                expanded_commands.extend([cmd] * count)
            except (IndexError, ValueError):
                print(colored("Invalid repetition format. Use -r <count>.", "red"))
                return []
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
        elif "|" in command:
            expanded_commands.extend([cmd.strip() for cmd in command.split("|")])
        else:
            expanded_commands.append(command)

    return expanded_commands * repeat if repeat > 1 else expanded_commands


# Command Functions
def print_status_snapshot() -> None:
    """Show status snapshot."""
    try:
        assert is_service_running(), "nexus service is not running"

        response = requests.get(f"{get_api_base_url()}/service/status")
        response.raise_for_status()
        status = response.json()

        queued = status.get("queued_jobs", 0)
        is_paused = status.get("is_paused", False)
        queue_status = "PAUSED" if is_paused else "RUNNING"
        queue_color = "yellow" if is_paused else "green"

        print(f"Queue: {queued} jobs pending [{colored(queue_status, queue_color)}]")
        print(f"History: {colored(str(status.get('completed_jobs', 0)), 'blue')} jobs completed\n")

        response = requests.get(f"{get_api_base_url()}/gpus")
        response.raise_for_status()
        gpus = response.json()

        print(colored("GPUs:", "white"))
        for gpu in gpus:
            gpu_info = f"GPU {gpu['index']} ({gpu['name']}, {gpu['memory_total']}MB): "
            if gpu.get("is_blacklisted"):
                gpu_info += colored("[BLACKLISTED] ", "red", attrs=["bold"])

            if gpu.get("running_job_id"):
                job_id = gpu["running_job_id"]
                response = requests.get(f"{get_api_base_url()}/jobs/{job_id}")
                response.raise_for_status()
                job = response.json()

                runtime = calculate_runtime(job)
                runtime_str = format_runtime(runtime)
                start_time = format_timestamp(job.get("started_at"))

                print(f"{gpu_info}{colored(job_id, 'magenta')}")
                print(f"  Command: {colored(job.get('command', 'N/A'), 'white', attrs=['bold'])}")
                print(f"  Runtime: {colored(runtime_str, 'cyan')}")
                print(f"  Started: {colored(start_time, 'cyan')}")
            else:
                print(f"{gpu_info}{colored('Available', 'green', attrs=['bold'])}")

    except requests.RequestException as e:
        print(colored(f"Error fetching status: {e}", "red"))


def add_jobs(commands: list[str], repeat: int = 1) -> None:
    """Add job(s) to the queue."""
    expanded_commands = expand_job_commands(commands, repeat)
    if not expanded_commands:
        return

    try:
        payload = {
            "commands": expanded_commands,
            "working_dir": os.getcwd(),
        }
        response = requests.post(f"{get_api_base_url()}/jobs", json=payload)
        response.raise_for_status()
        jobs = response.json()

        for job in jobs:
            print(f"Added job {colored(job['id'], 'magenta', attrs=['bold'])}: {job['command']}")
        print(colored(f"\nAdded {len(jobs)} jobs to the queue", "green", attrs=["bold"]))
    except requests.RequestException as e:
        print(colored(f"Error adding jobs: {e}", "red"))


def show_queue() -> None:
    """Show pending jobs."""
    try:
        response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "queued"})
        response.raise_for_status()
        jobs = response.json()

        if not jobs:
            print(colored("No pending jobs.", "green"))
            return

        print(colored("Pending Jobs:", "blue", attrs=["bold"]))
        total_jobs = len(jobs)
        for idx, job in enumerate(reversed(jobs), 1):
            created_time = format_timestamp(job.get("created_at"))
            print(
                f"{total_jobs - idx + 1}. {colored(job['id'], 'magenta')} - {colored(job['command'], 'white')} "
                f"(Added: {colored(created_time, 'cyan')})"
            )

        print(f"\n{colored('Total queued jobs:', 'blue', attrs=['bold'])} {colored(str(total_jobs), 'cyan')}")
    except requests.RequestException as e:
        print(colored(f"Error fetching queue: {e}", "red"))


def show_history() -> None:
    """Show completed jobs."""
    try:
        response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "completed"})
        response.raise_for_status()
        jobs = response.json()

        if not jobs:
            print(colored("No completed jobs.", "green"))
            return

        for job in jobs[-25:]:
            runtime = calculate_runtime(job)
            gpu = job.get("gpu_index", "Unknown")
            started_time = format_timestamp(job.get("started_at"))
            print(
                f"{colored(job['id'], 'magenta')}: "
                f"{colored(job['command'], 'white')} "
                f"(Started: {colored(started_time, 'cyan')}, "
                f"Runtime: {colored(format_runtime(runtime), 'cyan')}, "
                f"GPU: {colored(str(gpu), 'yellow')})"
            )

        total_jobs = len(jobs)
        if total_jobs > 25:
            print(
                f"\n{colored('Showing last 25 of', 'blue', attrs=['bold'])} "
                f"{colored(str(total_jobs), 'cyan')} "
                f"{colored('total completed jobs', 'blue', attrs=['bold'])}"
            )
    except requests.RequestException as e:
        print(colored(f"Error fetching history: {e}", "red"))


def kill_jobs(targets: list[str]) -> None:
    """Kill running jobs."""
    try:
        gpu_indices, job_ids = parse_targets(targets)
        jobs_to_kill = set()

        if gpu_indices:
            response = requests.get(f"{get_api_base_url()}/gpus")
            response.raise_for_status()
            gpus = response.json()

            for gpu_index in gpu_indices:
                matching_gpu = next((gpu for gpu in gpus if gpu["index"] == gpu_index), None)
                if not matching_gpu:
                    print(colored(f"No GPU found with index {gpu_index}", "red"))
                    continue

                job_id = matching_gpu.get("running_job_id")
                if job_id:
                    jobs_to_kill.add(job_id)
                else:
                    print(colored(f"No running job found on GPU {gpu_index}", "yellow"))

        if job_ids:
            response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "running"})
            response.raise_for_status()
            running_jobs = response.json()

            for pattern in job_ids:
                if pattern in [job["id"] for job in running_jobs]:
                    jobs_to_kill.add(pattern)
                else:
                    try:
                        regex = re.compile(pattern)
                        matching_jobs = [job["id"] for job in running_jobs if regex.search(job["command"])]
                        jobs_to_kill.update(matching_jobs)
                    except re.error as e:
                        print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))

        if not jobs_to_kill:
            print(colored("No matching running jobs found.", "yellow"))
            return

        response = requests.delete(f"{get_api_base_url()}/jobs/running", json=list(jobs_to_kill))
        response.raise_for_status()
        result = response.json()

        for job_id in result.get("killed", []):
            print(colored(f"Killed job {job_id}", "green"))
        for fail in result.get("failed", []):
            print(colored(f"Failed to kill job {fail['id']}: {fail['error']}", "red"))

    except requests.RequestException as e:
        if hasattr(e.response, "text"):
            assert e.response is not None
            print(colored(f"Error killing jobs: {e.response.text}", "red"))
        else:
            print(colored(f"Error killing jobs: {e}", "red"))


def remove_jobs(job_ids: list[str]) -> None:
    """Remove queued jobs."""
    try:
        response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "queued"})
        response.raise_for_status()
        queued_jobs = response.json()

        jobs_to_remove = set()
        for pattern in job_ids:
            if pattern in [job["id"] for job in queued_jobs]:
                jobs_to_remove.add(pattern)
            else:
                try:
                    regex = re.compile(pattern)
                    matching_jobs = [job["id"] for job in queued_jobs if regex.search(job["command"])]
                    jobs_to_remove.update(matching_jobs)
                except re.error as e:
                    print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))

        if not jobs_to_remove:
            print(colored("No matching queued jobs found.", "yellow"))
            return

        response = requests.delete(f"{get_api_base_url()}/jobs/queued", json=list(jobs_to_remove))
        response.raise_for_status()
        result = response.json()

        for job_id in result.get("removed", []):
            print(colored(f"Removed job {job_id}", "green"))
        for fail in result.get("failed", []):
            print(colored(f"Failed to remove job {fail['id']}: {fail['error']}", "red"))

    except requests.RequestException as e:
        if hasattr(e.response, "text"):
            assert e.response is not None
            print(colored(f"Error removing jobs: {e.response.text}", "red"))
        else:
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
        time.sleep(2)
        start_service()
    except Exception as e:
        print(colored(f"Error restarting service: {e}", "red"))


def view_logs(job_id: str) -> None:
    """View logs for a job or service."""
    try:
        if job_id == "service":
            response = requests.get(f"{get_api_base_url()}/service/logs")
            response.raise_for_status()
            print(colored("=== Service Logs ===", "blue", attrs=["bold"]))
            print(response.json().get("logs", ""))
        else:
            response = requests.get(f"{get_api_base_url()}/jobs/{job_id}/logs")
            response.raise_for_status()
            logs = response.json()
            print(colored("=== STDOUT ===", "blue", attrs=["bold"]))
            print(logs.get("stdout", ""))
            print(colored("\n=== STDERR ===", "red", attrs=["bold"]))
            print(logs.get("stderr", ""))
    except requests.RequestException as e:
        print(colored(f"Error fetching logs: {e}", "red"))


def attach_to_session(target: str) -> None:
    """Attach to screen session."""
    try:
        if target == "service":
            session_name = "nexus"
        elif target.isdigit():
            response = requests.get(f"{get_api_base_url()}/gpus")
            response.raise_for_status()
            gpus = response.json()

            gpu_index = int(target)
            matching_gpu = next((gpu for gpu in gpus if gpu["index"] == gpu_index), None)
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

        result = subprocess.run(["screen", "-ls"], capture_output=True, text=True, check=True)
        if session_name not in result.stdout:
            print(colored(f"No running screen session found for {session_name}", "red"))
            return

        subprocess.run(["screen", "-r", session_name], check=True)
    except (subprocess.CalledProcessError, requests.RequestException) as e:
        print(colored(f"Error accessing session: {e}", "red"))


def handle_blacklist(args) -> None:
    """Handle blacklist operations."""
    try:
        gpu_indexes = parse_gpu_list(args.gpus)

        response = requests.get(f"{get_api_base_url()}/gpus")
        response.raise_for_status()
        gpus = response.json()

        valid_indexes = {gpu["index"] for gpu in gpus}
        invalid_indexes = [idx for idx in gpu_indexes if idx not in valid_indexes]
        if invalid_indexes:
            print(colored(f"Invalid GPU indexes: {', '.join(map(str, invalid_indexes))}", "red"))
            return

        if args.blacklist_action == "add":
            response = requests.post(f"{get_api_base_url()}/gpus/blacklist", json=gpu_indexes)
        else:  # remove
            response = requests.delete(f"{get_api_base_url()}/gpus/blacklist", json=gpu_indexes)

        response.raise_for_status()
        result = response.json()

        action_word = "blacklisted" if args.blacklist_action == "add" else "removed from blacklist"
        successful = result.get("blacklisted" if args.blacklist_action == "add" else "removed", [])
        if successful:
            print(
                colored(
                    f"Successfully {action_word} GPUs: {', '.join(map(str, successful))}",
                    "green",
                )
            )

        failed = result.get("failed", [])
        if failed:
            print(colored(f"Failed to {action_word} some GPUs:", "red"))
            for fail in failed:
                print(colored(f"  GPU {fail['index']}: {fail['error']}", "red"))

    except requests.RequestException as e:
        print(colored(f"Error managing blacklist: {e}", "red"))
    except ValueError as e:
        print(colored(str(e), "red"))


def show_config() -> None:
    """Display current configuration."""
    try:
        config = load_config(DEFAULT_CONFIG_PATH)
        print(colored("Current Configuration:", "blue", attrs=["bold"]))

        # Format and display config entries
        for key, value in config.items():
            if isinstance(value, dict):
                print(f"\n{colored(key, 'white', attrs=['bold'])}:")
                for subkey, subvalue in value.items():
                    print(f"  {colored(subkey, 'cyan')}: {subvalue}")
            else:
                print(f"{colored(key, 'cyan')}: {value}")

    except Exception as e:
        print(colored(f"Error displaying config: {e}", "red"))


def show_version() -> None:
    """Display version information."""
    print(f"Nexus CLI version: {colored(VERSION, 'cyan')}")


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Basic commands
    subparsers.add_parser("status", help="Show status snapshot")
    subparsers.add_parser("stop", help="Stop the Nexus service")
    subparsers.add_parser("restart", help="Restart the Nexus service")
    subparsers.add_parser("queue", help="Show pending jobs")
    subparsers.add_parser("history", help="Show completed jobs")
    subparsers.add_parser("pause", help="Pause queue processing")
    subparsers.add_parser("resume", help="Resume queue processing")
    subparsers.add_parser("config", help="Show or edit configuration")
    subparsers.add_parser("version", help="Show version information")

    # Add jobs
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")

    # Kill jobs
    kill_parser = subparsers.add_parser("kill", help="Kill jobs by GPU indices, job IDs, or command regex")
    kill_parser.add_argument(
        "targets",
        nargs="+",
        help="List of GPU indices, job IDs, or command regex patterns",
    )

    # Remove jobs
    remove_parser = subparsers.add_parser("remove", help="Remove jobs from queue by job IDs or command regex")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")

    # Blacklist management
    blacklist_parser = subparsers.add_parser("blacklist", help="Manage GPU blacklist")
    blacklist_subparsers = blacklist_parser.add_subparsers(dest="blacklist_action", help="Blacklist commands", required=True)

    blacklist_add = blacklist_subparsers.add_parser("add", help="Add GPUs to blacklist")
    blacklist_add.add_argument("gpus", help="Comma-separated GPU indexes to blacklist (e.g., '0,1,2')")

    blacklist_remove = blacklist_subparsers.add_parser("remove", help="Remove GPUs from blacklist")
    blacklist_remove.add_argument("gpus", help="Comma-separated GPU indexes to remove from blacklist")

    # Logs
    logs_parser = subparsers.add_parser("logs", help="View logs for job or service")
    logs_parser.add_argument("id", help="Job ID or 'service' to view service logs")

    # Attach
    attach_parser = subparsers.add_parser("attach", help="Attach to screen session")
    attach_parser.add_argument("target", help="Job ID, GPU number, or 'service'")

    return parser


def main() -> None:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        start_service()
        print_status_snapshot()
        return

    command_handlers = {
        "status": lambda: print_status_snapshot(),
        "stop": lambda: stop_service(),
        "restart": lambda: restart_service(),
        "add": lambda: add_jobs(args.commands, args.repeat),
        "queue": lambda: show_queue(),
        "history": lambda: show_history(),
        "kill": lambda: kill_jobs(args.targets),
        "remove": lambda: remove_jobs(args.job_ids),
        "pause": lambda: pause_queue(),
        "resume": lambda: resume_queue(),
        "blacklist": lambda: handle_blacklist(args),
        "logs": lambda: view_logs(args.id),
        "attach": lambda: attach_to_session(args.target),
        "config": lambda: show_config(),
        "version": lambda: show_version(),
    }

    handler = command_handlers.get(args.command, parser.print_help)
    handler()


if __name__ == "__main__":
    main()
