import os
import re
import subprocess
import sys
import time

from termcolor import colored

from nexus.cli import api_client, config, setup, utils
from nexus.cli.config import NotificationType


def run_job(
    command: str,
    gpu_idxs_str: str | None = None,
    num_gpus: int | None = None,
    bypass_confirm: bool = False,
) -> None:
    """Run a job immediately on the server"""
    try:
        print(f"\n{colored('Running job immediately:', 'blue', attrs=['bold'])}")

        # Parse GPU indices if provided
        gpu_idxs = None
        gpu_info = ""

        if gpu_idxs_str:
            # If specific GPU indices are provided, use those
            gpu_idxs = utils.parse_gpu_list(gpu_idxs_str)
            gpu_info = f" on GPU(s): {colored(','.join(map(str, gpu_idxs)), 'cyan')}"
        elif num_gpus:
            # If number of GPUs is specified, note that in the message
            gpu_info = f" using {colored(str(num_gpus), 'cyan')} GPU(s)"

        print(f"  {colored('•', 'blue')} {command}{gpu_info}")

        if not utils.confirm_action(
            "Run this job immediately?",
            bypass=bypass_confirm,
        ):
            print(colored("Operation cancelled.", "yellow"))
            return

        # Prepare shared info
        cli_config = config.load_config()
        user = cli_config.user or "anonymous"

        # Set up notifications
        notifications = list(cli_config.default_notifications)

        # Generate a short random tag
        git_tag_id = utils.generate_git_tag_id()
        # Determine the current branch
        branch_name = utils.get_current_git_branch()

        # Attempt to create/push a git tag
        tag_name = f"nexus-{git_tag_id}"
        try:
            subprocess.run(["git", "tag", tag_name], check=True)
            # Mute push output
            subprocess.run(["git", "push", "origin", tag_name], check=True, stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            # Roll back the tag if push fails
            subprocess.run(["git", "tag", "-d", tag_name], check=False)
            raise RuntimeError(f"Failed to create/push git tag: {e}")

        # Derive remote URL
        result = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True)
        git_repo_url = result.stdout.strip() or "unknown-url"

        # Load environment variables
        env_vars = setup.load_current_env()

        # Determine number of GPUs needed
        gpus_count = len(gpu_idxs) if gpu_idxs else (num_gpus if num_gpus else 1)

        # Build the request payload with run_immediately=True
        job_request = {
            "command": command,
            "user": user,
            "git_repo_url": git_repo_url,
            "git_tag": tag_name,
            "git_branch": branch_name,
            "num_gpus": gpus_count,
            "priority": 0,  # Default priority
            "search_wandb": cli_config.search_wandb,
            "notifications": notifications,
            "env": env_vars,
            "jobrc": None,
            "gpu_idxs": gpu_idxs,
            "run_immediately": True,  # Key difference from add_jobs
        }

        result = api_client.add_job(job_request)
        job_id = result["id"]

        print(colored("\nJob started:", "green", attrs=["bold"]))
        print(f"  {colored('•', 'green')} Job {colored(job_id, 'magenta')}: {result['command']}")

        # Wait a moment for the job to actually start and create the screen session
        print(colored("\nWaiting for job to initialize...", "blue"))

        # Poll for the job to be fully started with a screen session
        max_attempts = 10
        for i in range(max_attempts):
            time.sleep(1)  # Wait 1 second between checks
            try:
                job = api_client.get_job(job_id)
                if job["status"] == "running" and job.get("screen_session_name"):
                    print(colored(f"Job {job_id} running, attaching to screen session...", "green"))
                    # Automatically attach to the job
                    attach_to_job(job_id)
                    return
            except Exception:
                pass  # Continue if there's any error checking the job

            # Show a progress indicator
            if i < max_attempts - 1:
                print(".", end="", flush=True)

        # If we couldn't attach, just show the regular message
        print(colored("\nCouldn't automatically attach to job. You can:", "yellow"))
        print(f"  - Run 'nx attach {job_id}' to attach to the job's screen session")
        print(f"  - Run 'nx logs {job_id}' to view the job output")
        print(f"  - Use 'nx logs -t 20 {job_id}' to see just the last 20 lines")

    except Exception as e:
        print(colored(f"\nError: {e}", "red"))
        sys.exit(1)


def add_jobs(
    commands: list[str],
    repeat: int,
    user: str | None,
    priority: int = 0,
    num_gpus: int = 1,
    notification_types: list[NotificationType] | None = None,
    bypass_confirm: bool = False,
) -> None:
    try:
        # Expand commands
        expanded_commands = utils.expand_job_commands(commands, repeat=repeat)
        if not expanded_commands:
            return

        # Show what will be added
        print(f"\n{colored('Adding the following jobs:', 'blue', attrs=['bold'])}")
        for cmd in expanded_commands:
            priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""
            gpus_str = f" (GPUs: {colored(str(num_gpus), 'cyan')})" if num_gpus > 1 else ""
            print(f"  {colored('•', 'blue')} {cmd}{priority_str}{gpus_str}")

        if not utils.confirm_action(
            f"Add {colored(str(len(expanded_commands)), 'cyan')} jobs to the queue?",
            bypass=bypass_confirm,
        ):
            print(colored("Operation cancelled.", "yellow"))
            return

        # Prepare shared info
        cli_config = config.load_config()
        final_user = user or cli_config.user or "anonymous"

        # Set up notifications - use default_notifications from config plus any additional ones specified
        notifications = list(cli_config.default_notifications)

        # Add any additional notification types if specified
        if notification_types:
            for notification_type in notification_types:
                if notification_type not in notifications:
                    notifications.append(notification_type)

        # Check if the user has configured the required environment variables for the notifications
        env_vars = setup.load_current_env()
        invalid_notifications = []

        for notification_type in notifications:
            required_vars = config.REQUIRED_ENV_VARS.get(notification_type, [])
            if any(env_vars.get(var) is None for var in required_vars):
                invalid_notifications.append(notification_type)

        if invalid_notifications:
            print(colored("\nWarning: Some notification types are missing required configuration:", "yellow"))
            for notification_type in invalid_notifications:
                print(f"  {colored('•', 'yellow')} {notification_type}")

            if not utils.ask_yes_no("Continue with remaining notification types?"):
                print(colored("Operation cancelled.", "yellow"))
                return

            # Remove invalid notification types
            notifications = [n for n in notifications if n not in invalid_notifications]

        # Generate a short random tag
        git_tag_id = utils.generate_git_tag_id()
        # Determine the current branch
        branch_name = utils.get_current_git_branch()

        # Attempt to create/push a git tag
        tag_name = f"nexus-{git_tag_id}"
        try:
            subprocess.run(["git", "tag", tag_name], check=True)
            # Mute push output
            subprocess.run(["git", "push", "origin", tag_name], check=True, stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            # Roll back the tag if push fails
            subprocess.run(["git", "tag", "-d", tag_name], check=False)
            raise RuntimeError(f"Failed to create/push git tag: {e}")

        # Derive remote URL (like the old code)
        result = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True)
        git_repo_url = result.stdout.strip() or "unknown-url"

        # Submit each command as a single job
        created_jobs = []
        for cmd in expanded_commands:
            # Build the request payload for the new API
            job_request = {
                "command": cmd,
                "user": final_user,
                "git_repo_url": git_repo_url,
                "git_tag": tag_name,
                "git_branch": branch_name,
                "num_gpus": num_gpus,
                "priority": priority,
                "search_wandb": cli_config.search_wandb,
                "notifications": notifications,
                "env": env_vars,
                "jobrc": None,
                "gpu_idxs": None,
                "run_immediately": False,  # New parameter in the API
            }

            result = api_client.add_job(job_request)
            created_jobs.append(result)

        # Summarize
        print(colored("\nSuccessfully added:", "green", attrs=["bold"]))
        for job in created_jobs:
            priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""
            gpus_str = f" (GPUs: {colored(str(num_gpus), 'cyan')})" if num_gpus > 1 else ""
            print(
                f"  {colored('•', 'green')} Job {colored(job['id'], 'magenta')}: {job['command']}{priority_str}{gpus_str}"
            )

    except Exception as e:
        print(colored(f"\nError: {e}", "red"))
        sys.exit(1)


def show_queue() -> None:
    try:
        # Use get_jobs with "queued" status instead of get_queue
        jobs = api_client.get_jobs("queued")

        if not jobs:
            print(colored("No pending jobs.", "green"))
            return

        print(colored("Pending Jobs:", "blue", attrs=["bold"]))
        total_jobs = len(jobs)
        for idx, job in enumerate(reversed(jobs), 1):
            created_time = utils.format_timestamp(job.get("created_at"))
            print(
                f"{total_jobs - idx + 1}. {colored(job['id'], 'magenta')} - "
                f"{colored(job['command'], 'white')} "
                f"(Added: {colored(created_time, 'cyan')})"
            )

        print(f"\n{colored('Total queued jobs:', 'blue', attrs=['bold'])} " f"{colored(str(total_jobs), 'cyan')}")
    except Exception as e:
        print(colored(f"Error fetching queue: {e}", "red"))


def show_history(regex: str | None = None) -> None:
    try:
        # Get completed, failed, and killed jobs
        statuses = ["completed", "failed", "killed"]
        jobs = []
        for status in statuses:
            jobs.extend(api_client.get_jobs(status))

        if not jobs:
            print(colored("No completed/failed/killed jobs.", "green"))
            return

        # Optional regex filter
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

        # Sort jobs by completion time, ascending
        jobs.sort(key=lambda x: x.get("completed_at", 0), reverse=False)

        print(colored("Job History:", "blue", attrs=["bold"]))
        # Show last 25
        for job in jobs[-25:]:
            runtime = utils.calculate_runtime(job)
            started_time = utils.format_timestamp(job.get("started_at"))
            status_color = (
                "green"
                if job["status"] == "completed"
                else "red"
                if job["status"] in ["failed", "killed"]
                else "yellow"
            )
            status_icon = (
                "✓"
                if job["status"] == "completed"
                else "✗"
                if job["status"] == "failed"
                else "🛑"
                if job["status"] == "killed"
                else "?"
            )
            status_str = colored(f"{status_icon} {job['status'].upper()}", status_color)

            # truncated command
            command = job["command"]
            if len(command) > 80:
                command = command[:77] + "..."

            print(
                f"{colored(job['id'], 'magenta')} [{status_str}] "
                f"{colored(command, 'white')} "
                f"(Started: {colored(started_time, 'cyan')}, "
                f"Runtime: {colored(utils.format_runtime(runtime), 'cyan')})"
            )

        total_jobs = len(jobs)
        if total_jobs > 25:
            print(f"\n{colored('Showing last 25 of', 'blue', attrs=['bold'])} " f"{colored(str(total_jobs), 'cyan')}")

        completed_count = sum(1 for j in jobs if j["status"] == "completed")
        failed_count = sum(1 for j in jobs if j["status"] == "failed")
        killed_count = sum(1 for j in jobs if j["status"] == "killed")
        print(
            f"\n{colored('Summary:', 'blue', attrs=['bold'])} "
            f"{colored(str(completed_count), 'green')} completed, "
            f"{colored(str(failed_count), 'red')} failed, "
            f"{colored(str(killed_count), 'red')} killed"
        )

    except Exception as e:
        print(colored(f"Error fetching history: {e}", "red"))


def kill_jobs(targets: list[str], bypass_confirm: bool = False) -> None:
    try:
        gpu_indices, job_ids = utils.parse_targets(targets)
        jobs_to_kill: set[str] = set()
        jobs_info: list[dict] = []

        # If user gave GPU indices, find which jobs are on those GPUs
        if gpu_indices:
            gpus = api_client.get_gpus()

            for gpu_idx in gpu_indices:
                gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
                if gmatch and gmatch.get("running_job_id"):
                    jobs_to_kill.add(gmatch["running_job_id"])
                    jobs_info.append(
                        {
                            "id": gmatch["running_job_id"],
                            "gpu_idx": gpu_idx,
                            "command": gmatch.get("command", ""),
                            "runtime": gmatch.get("runtime", ""),
                            "user": gmatch.get("user", ""),
                        }
                    )

        # If user gave job IDs or regex
        if job_ids:
            running_jobs = api_client.get_jobs("running")

            for pattern in job_ids:
                # First see if there's an exact match
                if any(j["id"] == pattern for j in running_jobs):
                    j = next(j for j in running_jobs if j["id"] == pattern)
                    jobs_to_kill.add(j["id"])
                    runtime = utils.calculate_runtime(j)
                    jobs_info.append(
                        {
                            "id": j["id"],
                            "command": j["command"],
                            "runtime": utils.format_runtime(runtime),
                            "user": j.get("user", ""),
                            "gpu_idx": j.get("gpu_idx"),
                        }
                    )
                else:
                    # Try to interpret as regex
                    try:
                        regex = re.compile(pattern)
                        matched = [j for j in running_jobs if regex.search(j["command"])]
                        for m in matched:
                            jobs_to_kill.add(m["id"])
                            runtime = utils.calculate_runtime(m)
                            jobs_info.append(
                                {
                                    "id": m["id"],
                                    "command": m["command"],
                                    "runtime": utils.format_runtime(runtime),
                                    "user": m.get("user", ""),
                                    "gpu_idx": m.get("gpu_idx"),
                                }
                            )
                    except re.error as e:
                        print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))

        if not jobs_to_kill:
            print(colored("No matching running jobs found.", "yellow"))
            return

        # Confirm
        print(f"\n{colored('The following jobs will be killed:', 'blue', attrs=['bold'])}")
        for info in jobs_info:
            job_details = [
                f"Job {colored(info['id'], 'magenta')}",
                f"Command: {info['command'][:50]}{'...' if len(info['command']) > 50 else ''}",
            ]

            if info["runtime"]:
                job_details.append(f"Runtime: {colored(info['runtime'], 'cyan')}")

            if info["user"]:
                job_details.append(f"User: {colored(info['user'], 'cyan')}")

            if info.get("gpu_idx") is not None:
                job_details.insert(0, f"GPU {info['gpu_idx']}")

            print(f"  {colored('•', 'blue')} {' | '.join(job_details)}")

        if not utils.confirm_action(f"Kill {colored(str(len(jobs_to_kill)), 'cyan')} jobs?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        # Issue delete
        result = api_client.kill_running_jobs(list(jobs_to_kill))

        print(colored("\nOperation results:", "green", attrs=["bold"]))
        for job_id in result.get("killed", []):
            info = next((i for i in jobs_info if i["id"] == job_id), None)
            if info:
                user_str = f" (User: {info['user']})" if info["user"] else ""
                runtime_str = f" (Runtime: {info['runtime']})" if info["runtime"] else ""
                print(
                    f"  {colored('•', 'green')} Successfully killed job {colored(job_id, 'magenta')}{user_str}{runtime_str}"
                )
            else:
                print(f"  {colored('•', 'green')} Successfully killed job {colored(job_id, 'magenta')}")

        for fail in result.get("failed", []):
            print(f"  {colored('×', 'red')} Failed to kill job {colored(fail['id'], 'magenta')}: {fail['error']}")

    except Exception as e:
        print(colored(f"Error killing jobs: {e}", "red"))


def remove_jobs(job_ids: list[str], bypass_confirm: bool = False) -> None:
    try:
        # Get queued jobs
        queued_jobs = api_client.get_jobs("queued")

        jobs_to_remove: set[str] = set()
        jobs_info: list[dict] = []

        for pattern in job_ids:
            # Direct ID match
            if any(j["id"] == pattern for j in queued_jobs):
                j = next(jj for jj in queued_jobs if jj["id"] == pattern)
                jobs_to_remove.add(pattern)
                created_time = utils.format_timestamp(j.get("created_at"))
                jobs_info.append(
                    {
                        "id": j["id"],
                        "command": j["command"],
                        "queue_time": created_time,
                        "user": j.get("user", ""),
                        "priority": j.get("priority", 0),
                    }
                )
            else:
                # Regex match
                try:
                    regex = re.compile(pattern)
                    matched = [jj for jj in queued_jobs if regex.search(jj["command"])]
                    for m in matched:
                        jobs_to_remove.add(m["id"])
                        created_time = utils.format_timestamp(m.get("created_at"))
                        jobs_info.append(
                            {
                                "id": m["id"],
                                "command": m["command"],
                                "queue_time": created_time,
                                "user": m.get("user", ""),
                                "priority": m.get("priority", 0),
                            }
                        )
                except re.error as e:
                    print(colored(f"Invalid regex pattern '{pattern}': {e}", "red"))

        if not jobs_to_remove:
            print(colored("No matching queued jobs found.", "yellow"))
            return

        print(f"\n{colored('The following jobs will be removed from queue:', 'blue', attrs=['bold'])}")
        for info in jobs_info:
            job_details = [
                f"Job {colored(info['id'], 'magenta')}",
                f"Command: {info['command'][:50]}{'...' if len(info['command']) > 50 else ''}",
            ]

            if info["queue_time"]:
                job_details.append(f"Queued: {colored(info['queue_time'], 'cyan')}")

            if info["user"]:
                job_details.append(f"User: {colored(info['user'], 'cyan')}")

            if info["priority"] != 0:
                job_details.append(f"Priority: {colored(str(info['priority']), 'cyan')}")

            print(f"  {colored('•', 'blue')} {' | '.join(job_details)}")

        if not utils.confirm_action(
            f"Remove {colored(str(len(jobs_to_remove)), 'cyan')} jobs from queue?", bypass=bypass_confirm
        ):
            print(colored("Operation cancelled.", "yellow"))
            return

        result = api_client.remove_queued_jobs(list(jobs_to_remove))

        print(colored("\nOperation results:", "green", attrs=["bold"]))
        for job_id in result.get("removed", []):
            info = next((i for i in jobs_info if i["id"] == job_id), None)
            if info:
                user_str = f" (User: {info['user']})" if info["user"] else ""
                queue_str = f" (Queued: {info['queue_time']})" if info["queue_time"] else ""
                print(
                    f"  {colored('•', 'green')} Successfully removed job {colored(job_id, 'magenta')}{user_str}{queue_str}"
                )
            else:
                print(f"  {colored('•', 'green')} Successfully removed job {colored(job_id, 'magenta')}")

        for fail in result.get("failed", []):
            print(f"  {colored('×', 'red')} Failed to remove job {colored(fail['id'], 'magenta')}: {fail['error']}")

    except Exception as e:
        print(colored(f"Error removing jobs: {e}", "red"))


def view_logs(target: str, tail: int = None) -> None:
    try:
        # Check if target is a GPU index
        if target.isdigit():
            gpu_idx = int(target)
            gpus = api_client.get_gpus()

            gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
            if not gmatch:
                print(colored(f"No GPU found with index {gpu_idx}", "red"))
                return

            job_id = gmatch.get("running_job_id")
            if not job_id:
                print(colored(f"No running job found on GPU {gpu_idx}", "yellow"))
                return

            target = job_id

        # Now treat target as a job_id
        if tail:
            logs = api_client.get_job_logs(target, last_n_lines=tail)
            if logs:
                print(colored(f"Showing last {tail} lines:", "blue"))
                print(logs)
            else:
                print(colored("No logs available", "yellow"))
        else:
            logs = api_client.get_job_logs(target)
            print(logs)

    except Exception as e:
        print(colored(f"Error fetching logs: {e}", "red"))


def show_health() -> None:
    try:
        health = api_client.get_detailed_health()

        print(colored("Node Health Status:", "blue", attrs=["bold"]))
        status = health.get("status", "unknown")
        status_color = "green" if status == "healthy" else "yellow" if status == "degraded" else "red"
        print(f"  {colored('•', 'blue')} Status: {colored(status, status_color)}")

        if health.get("score") is not None:
            score = health.get("score", 0)
            score_color = "green" if score > 0.8 else "yellow" if score > 0.5 else "red"
            print(f"  {colored('•', 'blue')} Health Score: {colored(f'{score:.2f}', score_color)}")

        # System stats
        if health.get("system"):
            system = health["system"]
            print(colored("\nSystem Statistics:", "blue", attrs=["bold"]))

            cpu_percent = system.get("cpu_percent", 0)
            cpu_color = "green" if cpu_percent < 70 else "yellow" if cpu_percent < 90 else "red"
            print(f"  {colored('•', 'blue')} CPU Usage: {colored(f'{cpu_percent:.1f}%', cpu_color)}")

            memory_percent = system.get("memory_percent", 0)
            memory_color = "green" if memory_percent < 70 else "yellow" if memory_percent < 90 else "red"
            print(f"  {colored('•', 'blue')} Memory Usage: {colored(f'{memory_percent:.1f}%', memory_color)}")

            uptime = system.get("uptime", 0)
            days = uptime // (24 * 3600)
            hours = (uptime % (24 * 3600)) // 3600
            minutes = (uptime % 3600) // 60
            uptime_str = f"{days}d {hours}h {minutes}m"
            print(f"  {colored('•', 'blue')} System Uptime: {colored(uptime_str, 'cyan')}")

            if system.get("load_avg"):
                load_avg = system["load_avg"]
                load_str = ", ".join([f"{x:.2f}" for x in load_avg])
                print(f"  {colored('•', 'blue')} Load Average: {colored(load_str, 'cyan')}")

        # Disk stats
        if health.get("disk"):
            disk = health["disk"]
            print(colored("\nDisk Statistics:", "blue", attrs=["bold"]))

            # Convert to GB for better readability
            total_gb = disk.get("total", 0) / (1024**3)
            used_gb = disk.get("used", 0) / (1024**3)
            free_gb = disk.get("free", 0) / (1024**3)
            percent_used = disk.get("percent_used", 0)

            disk_color = "green" if percent_used < 70 else "yellow" if percent_used < 90 else "red"
            print(
                f"  {colored('•', 'blue')} Disk Usage: {colored(f'{percent_used:.1f}%', disk_color)} "
                f"({colored(f'{used_gb:.1f}GB', 'cyan')} / {colored(f'{total_gb:.1f}GB', 'cyan')})"
            )
            print(f"  {colored('•', 'blue')} Free Space: {colored(f'{free_gb:.1f}GB', 'cyan')}")

        # Network stats
        if health.get("network"):
            network = health["network"]
            print(colored("\nNetwork Statistics:", "blue", attrs=["bold"]))

            download_speed = network.get("download_speed", 0)
            download_mb = download_speed / (1024**2)
            print(f"  {colored('•', 'blue')} Download Speed: {colored(f'{download_mb:.2f} MB/s', 'cyan')}")

            upload_speed = network.get("upload_speed", 0)
            upload_mb = upload_speed / (1024**2)
            print(f"  {colored('•', 'blue')} Upload Speed: {colored(f'{upload_mb:.2f} MB/s', 'cyan')}")

            ping = network.get("ping", 0)
            ping_color = "green" if ping < 50 else "yellow" if ping < 100 else "red"
            print(f"  {colored('•', 'blue')} Ping: {colored(f'{ping:.1f} ms', ping_color)}")

    except Exception as e:
        print(colored(f"Error fetching health information: {e}", "red"))


def update_job_command(
    job_id: str, command: str | None = None, priority: int | None = None, bypass_confirm: bool = False
) -> None:
    try:
        # Fetch the job first to show before/after
        job = api_client.get_job(job_id)

        if not job:
            print(colored(f"Job {job_id} not found", "red"))
            return

        # Only queued jobs can be updated
        if job["status"] != "queued":
            print(colored(f"Only queued jobs can be updated. Job {job_id} has status: {job['status']}", "red"))
            return

        print(f"\n{colored('Current job details:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} ID: {colored(job_id, 'magenta')}")
        print(f"  {colored('•', 'blue')} Command: {colored(job['command'], 'white')}")
        print(f"  {colored('•', 'blue')} Priority: {colored(str(job['priority']), 'cyan')}")

        # Show what will be updated
        print(f"\n{colored('Will update to:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} Command: {colored(command if command is not None else 'unchanged', 'white')}")
        print(
            f"  {colored('•', 'blue')} Priority: {colored(str(priority) if priority is not None else 'unchanged', 'cyan')}"
        )

        if not utils.confirm_action("Update this job?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        # Make the API call to update the job
        result = api_client.update_job(job_id, command, priority)

        print(colored("\nJob updated successfully:", "green", attrs=["bold"]))
        print(f"  {colored('•', 'green')} ID: {colored(result['id'], 'magenta')}")
        print(f"  {colored('•', 'green')} Command: {colored(result['command'], 'white')}")
        print(f"  {colored('•', 'green')} Priority: {colored(str(result['priority']), 'cyan')}")

    except Exception as e:
        print(colored(f"\nError updating job: {e}", "red"))
        sys.exit(1)


def handle_blacklist(args) -> None:
    try:
        gpu_idxs = utils.parse_gpu_list(args.gpus)
        gpus = api_client.get_gpus()

        valid_idxs = {gpu["index"] for gpu in gpus}
        invalid_idxs = [idx for idx in gpu_idxs if idx not in valid_idxs]
        if invalid_idxs:
            print(colored(f"Invalid GPU idxs: {', '.join(map(str, invalid_idxs))}", "red"))
            return

        result = api_client.manage_blacklist(gpu_idxs, args.blacklist_action)

        action_word = "blacklisted" if args.blacklist_action == "add" else "removed from blacklist"
        successful = result.get("blacklisted" if args.blacklist_action == "add" else "removed", [])
        if successful:
            print(colored(f"Successfully {action_word} GPUs: {', '.join(map(str, successful))}", "green"))

        failed = result.get("failed", [])
        if failed:
            print(colored(f"Failed to {action_word} some GPUs:", "red"))
            for fail in failed:
                print(colored(f"  GPU {fail['index']}: {fail['error']}", "red"))

    except Exception as e:
        print(colored(f"Error managing blacklist: {e}", "red"))


def print_status() -> None:
    try:
        # Check server status
        import importlib.metadata

        if not api_client.check_api_connection():
            raise RuntimeError("Cannot connect to Nexus API")

        try:
            VERSION = importlib.metadata.version("nexusai")
        except importlib.metadata.PackageNotFoundError:
            VERSION = "unknown"

        status = api_client.get_server_status()

        server_version = status.get("server_version", "unknown")
        if server_version != VERSION:
            print(
                colored(
                    f"WARNING: Nexus client version ({VERSION}) does not match "
                    f"Nexus server version ({server_version}).",
                    "yellow",
                )
            )

        queued = status.get("queued_jobs", 0)
        running = status.get("running_jobs", 0)
        completed = status.get("completed_jobs", 0)

        print(f"Queue: {queued} jobs pending")
        print(f"Running: {running} jobs in progress")
        print(f"History: {colored(str(completed), 'blue')} jobs completed\n")

        # GPU details
        gpus = api_client.get_gpus()

        print(colored("GPUs:", "white"))
        for gpu in gpus:
            memory_used = gpu.get("memory_used", 0)
            gpu_info = f"GPU {gpu['index']} ({gpu['name']}): [{memory_used}/{gpu['memory_total']}MB] "

            if gpu.get("is_blacklisted"):
                gpu_info += colored("[BLACKLISTED] ", "red", attrs=["bold"])

            if gpu.get("running_job_id"):
                job_id = gpu["running_job_id"]

                # Get specific job details directly with the new function
                job = api_client.get_job(job_id)

                runtime = utils.calculate_runtime(job)
                runtime_str = utils.format_runtime(runtime)
                start_time = utils.format_timestamp(job.get("started_at"))

                print(f"{gpu_info}{colored(job_id, 'magenta')}")
                print(f"  Command: {colored(job.get('command', ''), 'white', attrs=['bold'])}")
                print(f"  Time: {colored(runtime_str, 'cyan')} (Started: {colored(start_time, 'cyan')})")
                if job.get("wandb_url"):
                    print(f"  W&B: {colored(job['wandb_url'], 'yellow')}")

            elif gpu.get("is_blacklisted", False):
                print(f"{gpu_info}{colored('Blacklisted', 'red', attrs=['bold'])}")
            elif gpu.get("process_count", 0) > 0:
                print(f"{gpu_info}{colored('In Use (External Process)', 'yellow', attrs=['bold'])}")
            else:
                print(f"{gpu_info}{colored('Available', 'green', attrs=['bold'])}")

    except Exception as e:
        print(colored(f"Error: {e}", "red"))


def attach_to_job(target: str) -> None:
    """
    Attach to a running job's screen session. Can take either a job ID or a GPU index.
    If GPU index is provided, it will find the job running on that GPU.
    """
    try:
        # Check if target is a GPU index
        if target.isdigit():
            gpu_idx = int(target)
            gpus = api_client.get_gpus()

            gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
            if not gmatch:
                print(colored(f"No GPU found with index {gpu_idx}", "red"))
                return

            job_id = gmatch.get("running_job_id")
            if not job_id:
                print(colored(f"No running job found on GPU {gpu_idx}", "yellow"))
                return

            target = job_id

        # Now treat target as a job_id
        job = api_client.get_job(target)
        if not job:
            print(colored(f"Job {target} not found", "red"))
            return

        if job["status"] != "running":
            print(colored(f"Cannot attach to job with status: {job['status']}. Job must be running.", "red"))
            return

        # Get the screen session name
        screen_session_name = job.get("screen_session_name")
        if not screen_session_name:
            print(colored(f"No screen session found for job {target}", "red"))
            return

        print(colored(f"Attaching to job {target} screen session '{screen_session_name}'", "blue"))
        print(colored("Press Ctrl+A Ctrl+D to detach from the screen session", "blue"))

        # Give user time to read the instructions
        time.sleep(1)

        # Attach to the screen session
        os.system(f"screen -r {screen_session_name}")

        print(colored(f"Detached from job {target} screen session", "blue"))

    except Exception as e:
        print(colored(f"Error attaching to job: {e}", "red"))
