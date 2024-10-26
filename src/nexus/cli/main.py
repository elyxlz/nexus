import argparse
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


CONFIG = load_config(DEFAULT_CONFIG_PATH)
API_BASE_URL = f"http://{CONFIG['service']['host']}:{CONFIG['service']['port']}/v1"


# Define allowed colors as typing.Literal types
Color = typing.Literal[
    "grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"
]

# Define allowed attributes as typing.Literal types
Attribute = typing.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]


def colored_text(text: str, color: Color, attrs: list[Attribute] | None = None) -> str:
    """Return colored text with optional attributes."""
    return colored(text, color, attrs=attrs)


def print_status_snapshot():
    """Show status snapshot (non-interactive)."""
    try:
        response = requests.get(f"{API_BASE_URL}/service/status")
        response.raise_for_status()
        status = response.json()

        queued = status.get("queued_jobs", 0)
        status.get("running_jobs", 0)
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
        gpus_response = requests.get(f"{API_BASE_URL}/gpus")
        gpus_response.raise_for_status()
        gpus = gpus_response.json()

        print(f"{colored_text('GPUs', 'white')}:")
        for gpu in gpus:
            gpu_info = f"GPU {gpu['index']} ({gpu['name']}, {gpu['memory_total']}MB): "
            if gpu.get("running_job_id"):
                job_id = colored_text(gpu["running_job_id"], "magenta")
                command = colored_text(
                    gpu.get("command", "N/A"), "white", attrs=["bold"]
                )
                runtime = colored_text(format_runtime(gpu.get("runtime", 0)), "cyan")
                start_time = colored_text(
                    format_timestamp(gpu.get("started_at")), "cyan"
                )
                gpu_info += f"{job_id}\n  {colored_text('Command', 'white')}: {command}\n  {colored_text('Runtime', 'cyan')}: {runtime}\n  {colored_text('Started', 'cyan')}: {start_time}"
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


def format_timestamp(timestamp: float | None) -> str:
    """Format timestamp to human-readable string."""
    if not timestamp:
        return "Unknown"
    return time.strftime("%H:%M", time.localtime(timestamp))


def stop_service():
    """Stop the Nexus service."""
    try:
        response = requests.post(f"{API_BASE_URL}/service/stop")
        response.raise_for_status()
        print(colored("Nexus service stopped.", "green"))
    except requests.RequestException as e:
        print(colored(f"Error stopping service: {e}", "red"))


def restart_service():
    """Restart the Nexus service."""
    try:
        stop_service()
        time.sleep(2)  # Wait for service to stop
        subprocess.run(["nexus", "start"], check=True)
        print(colored("Nexus service restarted.", "green"))
    except subprocess.CalledProcessError as e:
        print(colored(f"Error restarting service: {e}", "red"))


def add_jobs(commands: list[str], repeat: int = 1):
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
        response = requests.post(f"{API_BASE_URL}/jobs", json=payload)
        response.raise_for_status()
        jobs = response.json()
        for job in jobs:
            print(
                f"Added job {colored_text(job['id'], 'magenta', attrs=['bold'])}: {colored_text(job['command'], 'cyan')}"
            )
    except requests.RequestException as e:
        print(colored(f"Error adding jobs: {e}", "red"))


def show_queue():
    """Show pending jobs."""
    try:
        response = requests.get(f"{API_BASE_URL}/jobs", params={"status": "queued"})
        response.raise_for_status()
        jobs = response.json()
        if not jobs:
            print(colored("No pending jobs.", "green"))
            return
        print(colored("Pending Jobs:", "blue", attrs=["bold"]))
        for idx, job in enumerate(jobs, 1):
            print(
                f"{idx}. {colored_text(job['id'], 'magenta')} - {colored_text(job['command'], 'white')}"
            )
    except requests.RequestException as e:
        print(colored(f"Error fetching queue: {e}", "red"))


def show_history():
    """Show completed jobs."""
    try:
        response = requests.get(f"{API_BASE_URL}/jobs", params={"status": "completed"})
        response.raise_for_status()
        jobs = response.json()
        if not jobs:
            print(colored("No completed jobs.", "green"))
            return
        print(colored("Completed Jobs:", "blue", attrs=["bold"]))
        for job in jobs:
            runtime = format_runtime(job.get("runtime", 0))
            gpu = job.get("gpu_index", "Unknown")
            print(
                f"{colored_text(job['id'], 'magenta')}: {colored_text(job['command'], 'white')} (Runtime: {colored_text(runtime, 'cyan')}, GPU: {colored_text(str(gpu), 'yellow')})"
            )
    except requests.RequestException as e:
        print(colored(f"Error fetching history: {e}", "red"))


def kill_jobs(pattern: str):
    """Kill job(s) by ID, GPU number, or command regex."""
    try:
        # Determine if pattern is GPU index
        if pattern.isdigit():
            gpu_index = int(pattern)
            response = requests.post(
                f"{API_BASE_URL}/jobs/kill", json={"gpu_index": gpu_index}
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
                f"{API_BASE_URL}/jobs", params={"status": "running"}
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
                f"{API_BASE_URL}/jobs/kill", json={"job_ids": matched_jobs}
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


def remove_jobs(pattern: str):
    """Remove job(s) from queue by ID or command regex."""
    try:
        response = requests.get(f"{API_BASE_URL}/jobs", params={"status": "queued"})
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
            f"{API_BASE_URL}/jobs/queued", json={"job_ids": matched_jobs}
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


def pause_queue():
    """Pause queue processing."""
    try:
        response = requests.post(f"{API_BASE_URL}/service/pause")
        response.raise_for_status()
        print(colored("Queue processing paused.", "yellow"))
    except requests.RequestException as e:
        print(colored(f"Error pausing queue: {e}", "red"))


def resume_queue():
    """Resume queue processing."""
    try:
        response = requests.post(f"{API_BASE_URL}/service/resume")
        response.raise_for_status()
        print(colored("Queue processing resumed.", "green"))
    except requests.RequestException as e:
        print(colored(f"Error resuming queue: {e}", "red"))


def view_logs(job_id: str | None):
    """View logs for a job or service."""
    try:
        if job_id == "service":
            response = requests.get(f"{API_BASE_URL}/service/logs")
            response.raise_for_status()
            logs = response.json().get("logs", "")
            print(colored("=== Service Logs ===", "blue", attrs=["bold"]))
            print(logs)
        else:
            response = requests.get(f"{API_BASE_URL}/jobs/{job_id}/logs")
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


def attach_to_session(target: str):
    """Attach to running job's screen session or service."""
    if target == "service":
        session_name = "nexus"
    elif target.isdigit():
        session_name = f"nexus_job_gpu_{target}"
    else:
        session_name = f"nexus_job_{target}"

    try:
        # Check if the session exists
        subprocess.run(
            ["screen", "-ls"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Attach to the session
        subprocess.run(["screen", "-r", session_name], check=True)
    except subprocess.CalledProcessError:
        print(colored(f"No running session found for {target}.", "red"))


def view_config():
    """View current configuration."""
    try:
        with open(DEFAULT_CONFIG_PATH, "r") as f:
            config_content = f.read()
        print(colored("Current Configuration:", "blue", attrs=["bold"]))
        print(config_content)
    except FileNotFoundError:
        print(colored("Configuration file not found.", "red"))


def edit_config():
    """Edit configuration in $EDITOR."""
    editor = os.environ.get("EDITOR", "vim")
    try:
        subprocess.run([editor, str(DEFAULT_CONFIG_PATH)], check=True)
    except subprocess.CalledProcessError as e:
        print(colored(f"Error editing config: {e}", "red"))


def show_help(command: str | None):
    """Show help for a specific command or general help."""
    parser.print_help()


def main():
    global parser
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus: GPU Job Management CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # nexus status
    subparsers.add_parser("status", help="Show status snapshot (non-interactive)")

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
        "logs": lambda: view_logs(args.id),
        "attach": lambda: attach_to_session(args.target),
        "config": lambda: edit_config() if args.action == "edit" else view_config(),
        "help": lambda: show_help(args.command),
    }

    handler = command_handlers.get(args.command, parser.print_help)
    handler()


if __name__ == "__main__":
    main()
