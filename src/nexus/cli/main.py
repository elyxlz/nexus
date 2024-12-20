import argparse
import hashlib
import importlib.metadata
import itertools
import os
import re
import subprocess
import sys
import time
import typing

import base58
import requests
from termcolor import colored

from nexus.cli.config import load_config

# Types
Color = typing.Literal["grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
Attribute = typing.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]

try:
    VERSION = importlib.metadata.version("nexusai")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"


def generate_git_tag_id() -> str:
    """Generate a unique git tag ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def get_api_base_url() -> str:
    """Get API base URL from config."""
    config = load_config()
    return f"http://{config.host}:{config.port}/v1"


# Service Management
def is_service_running() -> bool:
    """Check if the Nexus service is running by pinging the API."""
    config = load_config()
    try:
        response = requests.get(f"http://{config.host}:{config.port}/v1/service/status", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def get_service_session_name() -> str:
    """Get the screen session name for the service."""
    config = load_config()
    return f"nexus_service_{config.port}"


def start_service() -> None:
    """Start the Nexus service if not running."""
    if is_service_running():
        return

    session_name = get_service_session_name()
    try:
        subprocess.run(["screen", "-dmS", session_name, "nexus-service"], check=True)

        # Wait for service to start (max 10 seconds)
        for _ in range(10):
            if is_service_running():
                print(colored("Nexus service started successfully.", "green"))
                return
            time.sleep(1)

        # If we get here, service didn't start properly
        raise RuntimeError("Service started but API is not responding")

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
        # Try to clean up the screen session if it exists
        try:
            subprocess.run(["screen", "-S", session_name, "-X", "quit"], check=False)
        except Exception:
            pass


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
        if "{" in command and "}" in command:
            param_str = re.findall(r"\{([^}]+)\}", command)
            if not param_str:
                expanded_commands.append(command)
                continue
            params = [p.strip().split(",") for p in param_str]
            for combo in itertools.product(*[[v.strip() for v in param] for param in params]):
                temp_cmd = command
                for value in combo:
                    temp_cmd = re.sub(r"\{[^}]+\}", value, temp_cmd, count=1)
                expanded_commands.append(temp_cmd)
        else:
            expanded_commands.append(command)

    return expanded_commands * repeat if repeat > 1 else expanded_commands


# Command Functions
def print_status_snapshot() -> None:
    """Show status snapshot."""
    try:
        assert is_service_running(), "nexus service is not running"

        # After fetching the service status in print_status_snapshot() or main()
        status = None
        try:
            response = requests.get(f"{get_api_base_url()}/service/status")
            response.raise_for_status()
            status = response.json()

            service_version = status.get("service_version", "unknown")
            if service_version != VERSION:
                print(colored(f"WARNING: Nexus client version ({VERSION}) does not match Nexus service version ({service_version}).", "yellow"))
        except requests.RequestException as e:
            print(colored(f"Error fetching version: {e}", "red"))
        assert status is not None

        queued = status.get("queued_jobs", 0)

        print(f"Queue: {queued} jobs pending")
        print(f"History: {colored(str(status.get('completed_jobs', 0)), 'blue')} jobs completed\n")

        response = requests.get(f"{get_api_base_url()}/gpus")
        response.raise_for_status()
        gpus = response.json()

        print(colored("GPUs:", "white"))
        for gpu in gpus:
            memory_used = gpu.get("memory_used", 0)
            gpu_info = f"GPU {gpu['index']} ({gpu['name']}): [{memory_used}/{gpu['memory_total']}MB] "

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
                print(f"  Time: {colored(runtime_str, 'cyan')} (Started: {colored(start_time, 'cyan')})")
                if job.get("wandb_url"):
                    print(f"  W&B: {colored(job['wandb_url'], 'yellow')}")

            elif gpu.get("is_available", False):
                print(f"{gpu_info}{colored('Available', 'green', attrs=['bold'])}")
            else:
                print(f"{gpu_info}{colored('In Use (External Process)', 'yellow', attrs=['bold'])}")

    except requests.RequestException as e:
        print(colored(f"Error fetching status: {e}", "red"))


def ensure_git_reproducibility(id: str, dirty: bool) -> tuple[str, str]:
    # Check for uncommitted changes
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.stdout.strip() and not dirty:
        changes = result.stdout.strip()
        raise RuntimeError(f"Uncommitted changes present in repository:\n{changes}\n" "Cannot create reproducible job without clean git state")

    # Get repository URL
    result = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True, check=True)
    git_repo_url = result.stdout.strip()

    # Create and push tag
    tag_name = f"nexus-{id}"
    try:
        subprocess.run(["git", "tag", tag_name], check=True)
        subprocess.run(["git", "push", "origin", tag_name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return git_repo_url, tag_name

    except subprocess.CalledProcessError as e:
        # Try to clean up if tag was created but not pushed
        subprocess.run(["git", "tag", "-d", tag_name], check=False)
        raise RuntimeError(f"Failed to create/push git tag: {e}")


def add_jobs(commands: list[str], repeat: int, dirty: bool, user: str | None, discord_id: str | None, bypass_confirm: bool = False) -> None:
    try:
        # Expand commands first to show what will be added
        expanded_commands = expand_job_commands(commands, repeat=repeat)
        if not expanded_commands:
            return

        # Display what will be added
        print(f"\n{colored('Adding the following jobs:', 'blue', attrs=['bold'])}")
        for cmd in expanded_commands:
            print(f"  {colored('•', 'blue')} {cmd}")

        if not confirm_action(f"Add {colored(str(len(expanded_commands)), 'cyan')} jobs to the queue?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        # Rest of the add_jobs function remains the same...
        cli_config = load_config()
        final_user = user or cli_config.user
        final_discord = discord_id or cli_config.discord_id
        git_tag_id = generate_git_tag_id()
        git_repo_url, tag_name = ensure_git_reproducibility(git_tag_id, dirty=dirty)

        payload = {
            "commands": expanded_commands,
            "git_repo_url": git_repo_url,
            "git_tag": tag_name,
            "user": final_user,
            "discord_id": final_discord,
        }

        response = requests.post(f"{get_api_base_url()}/jobs", json=payload)
        response.raise_for_status()
        jobs = response.json()

        print(colored("\nSuccessfully added:", "green", attrs=["bold"]))
        for job in jobs:
            print(f"  {colored('•', 'green')} Job {colored(job['id'], 'magenta')}: {job['command']}")

    except requests.RequestException as e:
        print(colored(f"\nError adding jobs: {e}", "red"))
        sys.exit(1)


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


def show_history(regex: str | None = None) -> None:
    """Show completed and failed jobs with optional regex filtering."""
    try:
        response = requests.get(f"{get_api_base_url()}/jobs", params={"status": ["completed", "failed"]})
        response.raise_for_status()
        jobs = response.json()

        if not jobs:
            print(colored("No completed or failed jobs.", "green"))
            return

        # Filter jobs by regex if provided
        if regex:
            try:
                pattern = re.compile(regex)
                jobs = [j for j in jobs if pattern.search(j["command"])]
                if not jobs:
                    print(colored(f"No jobs found matching pattern: {regex}", "yellow"))
                    return
            except re.error as e:
                print(colored(f"Invalid regex pattern: {e}", "red"))
                return

        # Sort jobs by completion time, most recent first
        jobs.sort(key=lambda x: x.get("completed_at", 0), reverse=False)

        print(colored("Job History:", "blue", attrs=["bold"]))
        for job in jobs[-25:]:  # Last 25 jobs
            # Calculate runtime and format timestamps
            runtime = calculate_runtime(job)
            gpu = job.get("gpu_index", "Unknown")
            started_time = format_timestamp(job.get("started_at"))

            # Format status with color and icon
            status_color = "green" if job["status"] == "completed" else "red"
            status_icon = "✓" if job["status"] == "completed" else "✗"
            status = colored(f"{status_icon} {job['status'].upper()}", status_color)

            # Format command, potentially truncating if too long
            command = job["command"]
            if len(command) > 80:
                command = command[:77] + "..."

            print(
                f"{colored(job['id'], 'magenta')} [{status}] "
                f"{colored(command, 'white')} "
                f"(Started: {colored(started_time, 'cyan')}, "
                f"Runtime: {colored(format_runtime(runtime), 'cyan')}, "
                f"GPU: {colored(str(gpu), 'yellow')})"
            )

        total_jobs = len(jobs)
        if total_jobs > 25:
            print(
                f"\n{colored('Showing last 25 of', 'blue', attrs=['bold'])} "
                f"{colored(str(total_jobs), 'cyan')} "
                f"{colored('total jobs', 'blue', attrs=['bold'])}"
            )

        # Print summary statistics
        completed_count = sum(1 for j in jobs if j["status"] == "completed")
        failed_count = sum(1 for j in jobs if j["status"] == "failed")
        print(
            f"\n{colored('Summary:', 'blue', attrs=['bold'])} "
            f"{colored(str(completed_count), 'green')} completed, "
            f"{colored(str(failed_count), 'red')} failed"
        )

    except requests.RequestException as e:
        print(colored(f"Error fetching history: {e}", "red"))


def kill_jobs(targets: list[str], bypass_confirm: bool = False) -> None:
    try:
        gpu_indices, job_ids = parse_targets(targets)
        jobs_to_kill = set()
        jobs_info = []

        # Collect information about jobs that will be killed
        if gpu_indices:
            response = requests.get(f"{get_api_base_url()}/gpus")
            response.raise_for_status()
            gpus = response.json()

            for gpu_index in gpu_indices:
                matching_gpu = next((gpu for gpu in gpus if gpu["index"] == gpu_index), None)
                if matching_gpu and matching_gpu.get("running_job_id"):
                    jobs_to_kill.add(matching_gpu["running_job_id"])
                    jobs_info.append(f"GPU {gpu_index}: Job {colored(matching_gpu['running_job_id'], 'magenta')}")

        if job_ids:
            response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "running"})
            response.raise_for_status()
            running_jobs = response.json()

            for pattern in job_ids:
                matching_jobs = []
                if pattern in [job["id"] for job in running_jobs]:
                    matching_jobs.append(next(job for job in running_jobs if job["id"] == pattern))
                else:
                    try:
                        regex = re.compile(pattern)
                        matching_jobs.extend(job for job in running_jobs if regex.search(job["command"]))
                    except re.error as e:
                        print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))
                        continue

                for job in matching_jobs:
                    jobs_to_kill.add(job["id"])
                    jobs_info.append(f"Job {colored(job['id'], 'magenta')}: {job['command']}")

        if not jobs_to_kill:
            print(colored("No matching running jobs found.", "yellow"))
            return

        # Display jobs that will be killed
        print(f"\n{colored('The following jobs will be killed:', 'blue', attrs=['bold'])}")
        for info in jobs_info:
            print(f"  {colored('•', 'blue')} {info}")

        if not confirm_action(f"Kill {colored(str(len(jobs_to_kill)), 'cyan')} jobs?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        response = requests.delete(f"{get_api_base_url()}/jobs/running", json=list(jobs_to_kill))
        response.raise_for_status()
        result = response.json()

        print(colored("\nOperation results:", "green", attrs=["bold"]))
        for job_id in result.get("killed", []):
            print(f"  {colored('•', 'green')} Successfully killed job {colored(job_id, 'magenta')}")
        for fail in result.get("failed", []):
            print(f"  {colored('×', 'red')} Failed to kill job {colored(fail['id'], 'magenta')}: {fail['error']}")

    except requests.RequestException as e:
        if hasattr(e.response, "text"):
            assert e.response is not None
            print(colored(f"Error killing jobs: {e.response.text}", "red"))
        else:
            print(colored(f"Error killing jobs: {e}", "red"))


def remove_jobs(job_ids: list[str], bypass_confirm: bool = False) -> None:
    try:
        response = requests.get(f"{get_api_base_url()}/jobs", params={"status": "queued"})
        response.raise_for_status()
        queued_jobs = response.json()

        jobs_to_remove = set()
        jobs_info = []

        for pattern in job_ids:
            if pattern in [job["id"] for job in queued_jobs]:
                job = next(job for job in queued_jobs if job["id"] == pattern)
                jobs_to_remove.add(pattern)
                jobs_info.append(f"Job {colored(job['id'], 'magenta')}: {job['command']}")
            else:
                try:
                    regex = re.compile(pattern)
                    matching_jobs = [job for job in queued_jobs if regex.search(job["command"])]
                    for job in matching_jobs:
                        jobs_to_remove.add(job["id"])
                        jobs_info.append(f"Job {colored(job['id'], 'magenta')}: {job['command']}")
                except re.error as e:
                    print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))

        if not jobs_to_remove:
            print(colored("No matching queued jobs found.", "yellow"))
            return

        print(f"\n{colored('The following jobs will be removed from queue:', 'blue', attrs=['bold'])}")
        for info in jobs_info:
            print(f"  {colored('•', 'blue')} {info}")

        if not confirm_action(f"Remove {colored(str(len(jobs_to_remove)), 'cyan')} jobs from queue?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        response = requests.delete(f"{get_api_base_url()}/jobs/queued", json=list(jobs_to_remove))
        response.raise_for_status()
        result = response.json()

        print(colored("\nOperation results:", "green", attrs=["bold"]))
        for job_id in result.get("removed", []):
            print(f"  {colored('•', 'green')} Successfully removed job {colored(job_id, 'magenta')}")
        for fail in result.get("failed", []):
            print(f"  {colored('×', 'red')} Failed to remove job {colored(fail['id'], 'magenta')}: {fail['error']}")

    except requests.RequestException as e:
        if hasattr(e.response, "text"):
            assert e.response is not None
            print(colored(f"Error removing jobs: {e.response.text}", "red"))
        else:
            print(colored(f"Error removing jobs: {e}", "red"))


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


def view_logs(target: str) -> None:
    """View logs for a job, GPU, or service."""

    try:
        if target == "service":
            response = requests.get(f"{get_api_base_url()}/service/logs")
            response.raise_for_status()
            print(colored("=== Service Logs ===", "blue", attrs=["bold"]))
            print(response.json().get("logs", ""))
            return

        # Check if target is a GPU index
        if target.isdigit():
            gpu_index = int(target)
            response = requests.get(f"{get_api_base_url()}/gpus")
            response.raise_for_status()
            gpus = response.json()

            matching_gpu = next((gpu for gpu in gpus if gpu["index"] == gpu_index), None)
            if not matching_gpu:
                print(colored(f"No GPU found with index {gpu_index}", "red"))
                return

            job_id = matching_gpu.get("running_job_id")
            if not job_id:
                print(colored(f"No running job found on GPU {gpu_index}", "yellow"))
                return

            target = job_id

        # Get logs for the job
        response = requests.get(f"{get_api_base_url()}/jobs/{target}/logs")
        response.raise_for_status()
        logs = response.json()
        print(logs.get("logs", ""))
    except requests.RequestException as e:
        print(colored(f"Error fetching logs: {e}", "red"))


def attach_to_session(target: str) -> None:
    """Attach to screen session."""

    config = load_config()

    try:
        if target == "service":
            session_name = f"nexus_service_{config.port}"
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
        config = load_config()
        print(colored("Current Configuration:", "blue", attrs=["bold"]))

        # Format and display config entries
        for key, value in config.model_dump().items():
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
    print(f"Nexus version: {colored(VERSION, 'cyan')}")


def confirm_action(action_description: str, bypass: bool = False) -> bool:
    if bypass:
        return True

    response = input(f"\n{colored('?', 'blue', attrs=['bold'])} {action_description} [y/N] ").lower().strip()
    print()  # Add newline after response
    return response == "y"


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Basic commands
    subparsers.add_parser("stop", help="Stop the Nexus service")
    subparsers.add_parser("restart", help="Restart the Nexus service")
    subparsers.add_parser("queue", help="Show pending jobs")
    subparsers.add_parser("config", help="Show configuration")
    subparsers.add_parser("version", help="Show version information")

    # Add jobs
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")
    add_parser.add_argument("commands", nargs="+", help='Command to add, e.g., "python train.py"')
    add_parser.add_argument("-r", "--repeat", type=int, default=1, help="Repeat the command multiple times")
    add_parser.add_argument("-d", "--dirty", action="store_true", help="Allow adding jobs with unstaged changes")
    add_parser.add_argument("-u", "--user", help="Override default username")
    add_parser.add_argument("--discord_id", help="Override default Discord user ID")
    add_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Kill jobs
    kill_parser = subparsers.add_parser("kill", help="Kill jobs by GPU indices, job IDs, or command regex")
    kill_parser.add_argument(
        "targets",
        nargs="+",
        help="List of GPU indices, job IDs, or command regex patterns",
    )
    kill_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # Remove jobs
    remove_parser = subparsers.add_parser("remove", help="Remove jobs from queue by job IDs or command regex")
    remove_parser.add_argument("job_ids", nargs="+", help="List of job IDs or command regex patterns")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # History
    history_parser = subparsers.add_parser("history", help="Show completed and failed jobs")
    history_parser.add_argument("pattern", nargs="?", help="Filter jobs by command regex pattern")

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
        "stop": lambda: stop_service(),
        "restart": lambda: restart_service(),
        "add": lambda: add_jobs(
            args.commands, repeat=args.repeat, dirty=args.dirty, user=args.user, discord_id=args.discord_id, bypass_confirm=args.yes
        ),
        "queue": lambda: show_queue(),
        "history": lambda: show_history(getattr(args, "pattern", None)),
        "kill": lambda: kill_jobs(args.targets, bypass_confirm=args.yes),
        "remove": lambda: remove_jobs(args.job_ids, bypass_confirm=args.yes),
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
