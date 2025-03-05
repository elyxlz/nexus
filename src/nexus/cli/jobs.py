import re
import subprocess
import sys

from termcolor import colored

from nexus.cli import api_client, config, setup, utils
from nexus.cli.config import NotificationType


def add_jobs(
    commands: list[str],
    repeat: int,
    user: str | None,
    priority: int = 0,
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
            print(f"  {colored('•', 'blue')} {cmd}{priority_str}")

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
                "git_branch": branch_name,  # new field required by new API
                "num_gpus": 1,
                "priority": priority,
                "search_wandb": cli_config.search_wandb,
                "notifications": notifications,
                "env": env_vars,
                "jobrc": None,
                "gpu_idxs": None,
                "ignore_blacklist": False,
            }

            result = api_client.add_job(job_request)
            created_jobs.append(result)

        # Summarize
        print(colored("\nSuccessfully added:", "green", attrs=["bold"]))
        for job in created_jobs:
            priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""
            print(f"  {colored('•', 'green')} Job {colored(job['id'], 'magenta')}: {job['command']}{priority_str}")

    except Exception as e:
        print(colored(f"\nError: {e}", "red"))
        sys.exit(1)


def show_queue() -> None:
    try:
        jobs = api_client.get_queue()

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


def view_logs(target: str) -> None:
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
        logs = api_client.get_job_logs(target)
        print(logs)

    except Exception as e:
        print(colored(f"Error fetching logs: {e}", "red"))


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
                import requests

                jr = requests.get(f"{api_client.get_api_base_url()}/jobs/{job_id}")
                jr.raise_for_status()
                job = jr.json()

                runtime = utils.calculate_runtime(job)
                runtime_str = utils.format_runtime(runtime)
                start_time = utils.format_timestamp(job.get("started_at"))

                print(f"{gpu_info}{colored(job_id, 'magenta')}")
                print(f"  Command: {colored(job.get('command', 'white'), 'white', attrs=['bold'])}")
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
