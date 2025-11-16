import os
import re
import sys
import time

from termcolor import colored

from nexus.cli import api_client, config, setup, utils
from nexus.cli.config import IntegrationType, NotificationType


def run_job(
    cfg: config.NexusCliConfig,
    commands: list[str],
    gpu_idxs_str: str | None = None,
    num_gpus: int = 1,
    notification_types: list[NotificationType] | None = None,
    integration_types: list[config.IntegrationType] | None = None,
    force: bool = False,
    bypass_confirm: bool = False,
    interactive: bool = False,
    silent: bool = False,
    local: bool = False,
    target_name: str | None = None,
    cpu: bool = False,
) -> None:
    try:
        gpu_idxs = None
        gpu_info = ""

        if cpu:
            num_gpus = 0
            gpu_idxs = None
            gpu_info = f" on {colored('CPU', 'cyan')}"
        elif gpu_idxs_str:
            gpu_idxs = utils.parse_gpu_list(gpu_idxs_str)
            gpu_info = f" on GPU(s): {colored(','.join(map(str, gpu_idxs)), 'cyan')}"
        elif num_gpus:
            gpu_info = f" using {colored(str(num_gpus), 'cyan')} GPU(s)"

        if interactive:
            command = "bash"  # Use bash for interactive mode
            print(f"\n{colored('Starting interactive session:', 'blue', attrs=['bold'])}")
            print(f"  {colored('•', 'blue')} Interactive shell{gpu_info}")
        else:
            if not commands:
                print(colored("Error: No command specified for job", "red"))
                return
            command = " ".join(commands)
            print(f"\n{colored('Running job:', 'blue', attrs=['bold'])}")
            print(f"  {colored('•', 'blue')} {command}{gpu_info}")

        if not utils.confirm_action(
            "Run this job",
            bypass=bypass_confirm,
        ):
            print(colored("Operation cancelled.", "yellow"))
            return

        user = cfg.user or "anonymous"

        notifications = [] if silent else list(cfg.default_notifications)
        integrations = list(cfg.default_integrations)

        if notification_types:
            for notification_type in notification_types:
                if notification_type not in notifications:
                    notifications.append(notification_type)

        if integration_types:
            for integration_type in integration_types:
                if integration_type not in integrations:
                    integrations.append(integration_type)

        git_ctx = None
        try:
            git_ctx = utils.prepare_git_artifact(cfg.enable_git_tag_push and not local, target_name=target_name)
            global_env = setup.load_current_env()
            local_env = setup.load_local_env()
            job_env_vars, conflicts = setup.merge_env_with_conflicts(global_env, local_env)

            if local_env:
                print(colored(f"\nLoaded {len(local_env)} variable(s) from local .env file", "cyan"))
            if conflicts:
                print(colored(f"\nLocal .env overriding {len(conflicts)} global variable(s):", "yellow"))
                for key in conflicts.keys():
                    print(f"  {colored('•', 'yellow')} {key}")

            invalid_notifications = []

            for notification_type in notifications:
                required_vars = config.REQUIRED_ENV_VARS.get(notification_type, [])
                if any(job_env_vars.get(var) is None for var in required_vars):
                    invalid_notifications.append(notification_type)

            if invalid_notifications:
                print(colored("\nWarning: Some notification types are missing required configuration:", "yellow"))
                for notification_type in invalid_notifications:
                    print(f"  {colored('•', 'yellow')} {notification_type}")

                if not utils.ask_yes_no("Continue with remaining notification types?"):
                    print(colored("Operation cancelled.", "yellow"))
                    return

                notifications = [n for n in notifications if n not in invalid_notifications]

            gpus_count = len(gpu_idxs) if gpu_idxs else num_gpus

            jobrc_content = None
            jobrc_path = setup.get_jobrc_path()
            if jobrc_path.exists():
                with open(jobrc_path) as f:
                    jobrc_content = f.read()

            job_request = {
                "job_id": git_ctx.job_id,
                "command": command,
                "user": user,
                "artifact_id": git_ctx.artifact_id,
                "git_repo_url": git_ctx.git_repo_url,
                "git_branch": git_ctx.branch_name,
                "git_tag": git_ctx.git_tag,
                "num_gpus": gpus_count,
                "priority": 0,
                "integrations": integrations,
                "notifications": notifications,
                "env": job_env_vars,
                "jobrc": jobrc_content,
                "gpu_idxs": gpu_idxs,
                "run_immediately": True,
                "ignore_blacklist": force,
                "git_tag_pushed": bool(cfg.enable_git_tag_push and not local),
            }

            result = api_client.add_job(job_request, target_name=target_name)
            if "id" not in result:
                raise ValueError(f"API response missing 'id' field: {result}")
            job_id = result["id"]

            print(colored("\nJob started:", "green", attrs=["bold"]))
            print(f"  {colored('•', 'green')} Job {colored(job_id, 'magenta')}: {result['command']}")

            utils.cleanup_git_state(git_ctx)
            git_ctx = None

            print(colored("\nWaiting for job to initialize...", "blue"))

            max_attempts = 10
            for i in range(max_attempts):
                time.sleep(1)
                try:
                    job = api_client.get_job(job_id, target_name=target_name)
                    if job["status"] == "running" and job.get("screen_session_name"):
                        print(colored(f"Job {job_id} running, attaching to screen session...", "green"))
                        attach_to_job(cfg, job_id, target_name=target_name)
                        return
                except Exception:
                    pass

                if i < max_attempts - 1:
                    print(".", end="", flush=True)

            target_flag = f" -t {target_name}" if target_name else ""
            print(colored("\nCouldn't automatically attach to job. You can:", "yellow"))
            print(f"  - Run 'nx attach{target_flag} {job_id}' to attach to the job's screen session")
            print(f"  - Run 'nx logs{target_flag} {job_id}' to view the job output")
            print(f"  - Use 'nx logs -n 20{target_flag} {job_id}' to see just the last 20 lines")

        finally:
            if git_ctx:
                utils.cleanup_git_state(git_ctx)

    except Exception as e:
        print(colored(f"\nError: {e}", "red"))
        sys.exit(1)


def add_jobs(
    cfg: config.NexusCliConfig,
    commands: list[str],
    repeat: int,
    priority: int = 0,
    gpu_idxs_str: str | None = None,
    num_gpus: int = 1,
    notification_types: list[NotificationType] | None = None,
    integration_types: list[IntegrationType] | None = None,
    force: bool = False,
    bypass_confirm: bool = False,
    silent: bool = False,
    local: bool = False,
    target_name: str | None = None,
    cpu: bool = False,
) -> None:
    try:
        if not commands:
            print(colored("Error: No command provided", "red"))
            print(colored("Usage: nx add [flags] command", "yellow"))
            print(colored("Example: nx add -r 4 python train.py --lr 0.001", "yellow"))
            return

        command_str = " ".join(commands)
        commands_list = [command_str]

        gpu_idxs = None
        if cpu:
            num_gpus = 0
            gpu_idxs = None
        elif gpu_idxs_str:
            gpu_idxs = utils.parse_gpu_list(gpu_idxs_str)

        expanded_commands = utils.expand_job_commands(commands_list, repeat=repeat)
        if not expanded_commands:
            return

        print(f"\n{colored('Adding the following jobs:', 'blue', attrs=['bold'])}")
        for cmd in expanded_commands:
            priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""
            if cpu:
                gpus_str = " (CPU)"
            elif gpu_idxs:
                gpus_str = f" (GPUs: {colored(','.join(map(str, gpu_idxs)), 'cyan')})"
            elif num_gpus > 1:
                gpus_str = f" (GPUs: {colored(str(num_gpus), 'cyan')})"
            else:
                gpus_str = ""
            print(f"  {colored('•', 'blue')} {cmd}{priority_str}{gpus_str}")

        if not utils.confirm_action(
            f"Add {colored(str(len(expanded_commands)), 'cyan')} jobs to the queue?",
            bypass=bypass_confirm,
        ):
            print(colored("Operation cancelled.", "yellow"))
            return

        user = cfg.user or "anonymous"

        notifications = [] if silent else list(cfg.default_notifications)
        integrations = list(cfg.default_integrations)

        if notification_types:
            for notification_type in notification_types:
                if notification_type not in notifications:
                    notifications.append(notification_type)

        if integration_types:
            for integration_type in integration_types:
                if integration_type not in integrations:
                    integrations.append(integration_type)

        global_env = setup.load_current_env()
        local_env = setup.load_local_env()
        env_vars, conflicts = setup.merge_env_with_conflicts(global_env, local_env)

        if local_env:
            print(colored(f"\nLoaded {len(local_env)} variable(s) from local .env file", "cyan"))
        if conflicts:
            print(colored(f"\nLocal .env overriding {len(conflicts)} global variable(s):", "yellow"))
            for key in conflicts.keys():
                print(f"  {colored('•', 'yellow')} {key}")

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

            notifications = [n for n in notifications if n not in invalid_notifications]

        git_ctx = None
        try:
            git_ctx = utils.prepare_git_artifact(enable_git_tag_push=False, target_name=target_name)
            jobrc_content = None
            jobrc_path = setup.get_jobrc_path()
            if jobrc_path.exists():
                with open(jobrc_path) as f:
                    jobrc_content = f.read()

            created_jobs = []
            job_env_vars = dict(env_vars)
            gpus_count = len(gpu_idxs) if gpu_idxs else num_gpus
            for cmd in expanded_commands:
                queued_job_id = utils.generate_job_id()
                job_request = {
                    "job_id": queued_job_id,
                    "command": cmd,
                    "user": user,
                    "artifact_id": git_ctx.artifact_id,
                    "git_repo_url": git_ctx.git_repo_url,
                    "git_branch": git_ctx.branch_name,
                    "git_tag": git_ctx.git_tag,
                    "num_gpus": gpus_count,
                    "priority": priority,
                    "integrations": integrations,
                    "notifications": notifications,
                    "env": job_env_vars,
                    "jobrc": jobrc_content,
                    "gpu_idxs": gpu_idxs,
                    "run_immediately": False,
                    "ignore_blacklist": force,
                    "git_tag_pushed": False,
                }

                result = api_client.add_job(job_request, target_name=target_name)
                created_jobs.append(result)

            print(colored("\nSuccessfully added:", "green", attrs=["bold"]))
            for job in created_jobs:
                priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""
                if gpu_idxs:
                    gpus_str = f" (GPUs: {colored(','.join(map(str, gpu_idxs)), 'cyan')})"
                elif num_gpus > 1:
                    gpus_str = f" (GPUs: {colored(str(num_gpus), 'cyan')})"
                else:
                    gpus_str = ""
                print(
                    f"  {colored('•', 'green')} Job {colored(job['id'], 'magenta')}: {job['command']}{priority_str}{gpus_str}"
                )

        finally:
            if git_ctx:
                utils.cleanup_git_state(git_ctx)

    except Exception as e:
        print(colored(f"\nError: {e}", "red"))
        sys.exit(1)


def show_queue(target_name: str | None = None) -> None:
    try:
        jobs = api_client.get_jobs("queued", target_name=target_name)

        if not jobs:
            print(colored("No pending jobs.", "green"))
            return

        print(colored("Pending Jobs:", "blue", attrs=["bold"]))
        total_jobs = len(jobs)
        for idx, job in enumerate(reversed(jobs), 1):
            if "created_at" not in job or "priority" not in job or "num_gpus" not in job:
                raise ValueError(f"Job missing required fields: {job}")

            created_time = utils.format_timestamp(job["created_at"])
            priority = job["priority"]
            num_gpus = job["num_gpus"]
            gpu_idxs = job.get("gpu_idxs")

            priority_str = f" (Priority: {colored(str(priority), 'cyan')})" if priority != 0 else ""

            if gpu_idxs:
                gpu_str = f" (GPUs: {colored(','.join(map(str, gpu_idxs)), 'cyan')})"
            elif num_gpus > 1:
                gpu_str = f" (GPUs: {colored(str(num_gpus), 'cyan')})"
            else:
                gpu_str = ""

            print(
                f"{total_jobs - idx + 1}. {colored(job['id'], 'magenta')} - "
                f"{colored(job['command'], 'white')} "
                f"(Added: {colored(created_time, 'cyan')}){priority_str}{gpu_str}"
            )

        print(f"\n{colored('Total queued jobs:', 'blue', attrs=['bold'])} {colored(str(total_jobs), 'cyan')}")
    except Exception as e:
        print(colored(f"Error fetching queue: {e}", "red"))


def show_history(regex: str | None = None, target_name: str | None = None) -> None:
    try:
        statuses = ["completed", "failed", "killed"]
        jobs = []
        for status in statuses:
            jobs.extend(api_client.get_jobs(status, target_name=target_name))

        if not jobs:
            print(colored("No completed/failed/killed jobs.", "green"))
            return

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

        def get_sort_timestamp(job):
            if "completed_at" in job and job["completed_at"]:
                return job["completed_at"]
            if "started_at" in job and job["started_at"]:
                return job["started_at"]
            if "created_at" in job and job["created_at"]:
                return job["created_at"]
            return 0

        jobs.sort(key=get_sort_timestamp, reverse=True)

        print(colored("Job History:", "blue", attrs=["bold"]))
        for job in reversed(jobs[:25]):
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
            print(
                f"\n{colored('Showing most recent 25 of', 'blue', attrs=['bold'])} {colored(str(total_jobs), 'cyan')}"
            )

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


def kill_jobs(targets: list[str] | None = None, bypass_confirm: bool = False, target_name: str | None = None) -> None:
    try:
        jobs_to_kill: set[str] = set()
        jobs_info: list[dict] = []

        # If no targets provided, get the latest running job
        if not targets:
            running_jobs = api_client.get_jobs("running", target_name=target_name)

            if not running_jobs:
                print(colored("No running jobs found to kill.", "yellow"))
                return

            # Filter for jobs with valid started_at timestamps to prevent sorting errors
            valid_jobs = [j for j in running_jobs if j.get("started_at") is not None]
            if not valid_jobs:
                print(colored("No running jobs with valid start times found", "yellow"))
                return

            # Sort by started_at (newest first)
            valid_jobs.sort(key=lambda x: x.get("started_at", 0), reverse=True)
            latest_job = valid_jobs[0]
            job_id = latest_job["id"]

            # Ask for confirmation for the latest job
            runtime = utils.calculate_runtime(latest_job)
            runtime_str = utils.format_runtime(runtime) if runtime else "N/A"

            print(colored(f"Latest job found: {job_id}", "blue"))
            print(
                f"  {colored('•', 'blue')} Command: {latest_job['command'][:80]}{'...' if len(latest_job['command']) > 80 else ''}"
            )
            print(f"  {colored('•', 'blue')} Runtime: {colored(runtime_str, 'cyan')}")
            if latest_job.get("user"):
                print(f"  {colored('•', 'blue')} User: {colored(latest_job['user'], 'cyan')}")

            jobs_to_kill.add(job_id)
            jobs_info.append(
                {
                    "id": job_id,
                    "command": latest_job["command"],
                    "runtime": runtime_str,
                    "user": latest_job.get("user", ""),
                    "gpu_idx": latest_job.get("gpu_idx"),
                }
            )

        # Process provided targets
        else:
            gpu_indices, job_ids = utils.parse_targets(targets)

            if gpu_indices:
                gpus = api_client.get_gpus(target_name=target_name)
                running_jobs = api_client.get_jobs("running", target_name=target_name)

                for gpu_idx in gpu_indices:
                    gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
                    if gmatch and gmatch.get("running_job_id"):
                        job_id = gmatch["running_job_id"]
                        jobs_to_kill.add(job_id)

                        # Get the job details from running_jobs
                        job_match = next((j for j in running_jobs if j["id"] == job_id), None)
                        runtime = utils.calculate_runtime(job_match) if job_match else ""

                        jobs_info.append(
                            {
                                "id": job_id,
                                "gpu_idx": gpu_idx,
                                "command": job_match.get("command", "") if job_match else "",
                                "runtime": utils.format_runtime(runtime) if runtime else "",
                                "user": job_match.get("user", "") if job_match else "",
                            }
                        )

            if job_ids:
                running_jobs = api_client.get_jobs("running", target_name=target_name)

                for pattern in job_ids:
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

        print(f"\n{colored('The following jobs will be killed:', 'blue', attrs=['bold'])}")
        for info in jobs_info:
            job_details = [
                f"Job {colored(info['id'], 'magenta')}",
            ]

            if info["command"]:
                job_details.append(f"Command: {info['command'][:50]}{'...' if len(info['command']) > 50 else ''}")

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

        result = api_client.kill_running_jobs(list(jobs_to_kill), target_name=target_name)

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


def remove_jobs(job_ids: list[str], bypass_confirm: bool = False, target_name: str | None = None) -> None:
    try:
        queued_jobs = api_client.get_jobs("queued", target_name=target_name)

        jobs_to_remove: set[str] = set()
        jobs_info: list[dict] = []

        for pattern in job_ids:
            if any(j["id"] == pattern for j in queued_jobs):
                j = next(jj for jj in queued_jobs if jj["id"] == pattern)
                if pattern not in jobs_to_remove:
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
                try:
                    regex = re.compile(pattern)
                    matched = [jj for jj in queued_jobs if regex.search(jj["command"])]
                    for m in matched:
                        if m["id"] not in jobs_to_remove:
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

        result = api_client.remove_queued_jobs(list(jobs_to_remove), target_name=target_name)

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


def view_logs(
    cfg: config.NexusCliConfig, target: str | None = None, tail: int | None = None, target_name: str | None = None
) -> None:
    try:
        user = cfg.user or "anonymous"
        job_id: str = ""
        if target is None:
            jobs = []
            for status in ["running", "completed", "failed", "killed"]:
                jobs.extend(api_client.get_jobs(status, target_name=target_name))

            if not jobs:
                print(colored("No jobs found", "yellow"))
                return

            user_jobs = [j for j in jobs if j.get("user") == user]
            if not user_jobs:
                print(colored(f"No jobs found for user '{user}'", "yellow"))
                return

            valid_jobs = [job for job in user_jobs if job.get("started_at") is not None]
            if not valid_jobs:
                print(colored(f"No jobs with valid start times found for user '{user}'", "yellow"))
                return

            valid_jobs.sort(key=lambda x: x.get("started_at", 0), reverse=True)
            job_id = valid_jobs[0]["id"]
            job_status = valid_jobs[0]["status"]
            print(colored(f"Viewing logs for most recent job: {job_id} ({job_status})", "blue"))
        elif target.isdigit():
            gpu_idx = int(target)
            gpus = api_client.get_gpus(target_name=target_name)

            gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
            if not gmatch:
                print(colored(f"No GPU found with index {gpu_idx}", "red"))
                return

            gpu_job_id = gmatch.get("running_job_id")
            if not gpu_job_id:
                print(colored(f"No running job found on GPU {gpu_idx}", "yellow"))
                return

            job_id = gpu_job_id
        else:
            job_id = target

        job = api_client.get_job(job_id, target_name=target_name)
        if not job:
            print(colored(f"Job {job_id} not found", "red"))
            return

        if tail is None and job["status"] in ["completed", "failed", "killed"]:
            tail = 5000
            print(colored(f"Job {job_id} is {job['status']}. Showing last {tail} lines:", "blue"))

        if tail:
            logs = api_client.get_job_logs(job_id, last_n_lines=tail, target_name=target_name)
            if logs:
                print(logs)
            else:
                print(colored("No logs available", "yellow"))
        else:
            logs = api_client.get_job_logs(job_id, target_name=target_name)
            if logs:
                print(logs)
            else:
                print(colored("Log is empty", "yellow"))

    except Exception as e:
        print(colored(f"Error fetching logs: {e}", "red"))


def show_health(refresh: bool = False, target_name: str | None = None) -> None:
    try:
        health = api_client.get_detailed_health(refresh=refresh, target_name=target_name)

        print(colored("Node Health Status:", "blue", attrs=["bold"]))
        status = health.get("status", "unknown")
        status_color = "green" if status == "healthy" else "yellow" if status == "under_load" else "red"
        print(f"  {colored('•', 'blue')} Status: {colored(status, status_color)}")

        if status == "unhealthy":
            utils.print_health_warning()

        if health.get("score") is not None:
            score = health.get("score", 0)
            score_color = "green" if score > 0.8 else "yellow" if score > 0.5 else "red"
            print(f"  {colored('•', 'blue')} Health Score: {colored(f'{score:.2f}', score_color)}")

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

        if health.get("disk"):
            disk = health["disk"]
            print(colored("\nDisk Statistics:", "blue", attrs=["bold"]))

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

        if health.get("network"):
            network = health["network"]
            print(colored("\nNetwork Statistics:", "blue", attrs=["bold"]))

            download_speed = network.get("download_speed", 0)
            upload_speed = network.get("upload_speed", 0)
            ping = network.get("ping", 0)

            print(f"  {colored('•', 'blue')} Download Speed: {colored(f'{download_speed:.1f} Mbps', 'cyan')}")
            print(f"  {colored('•', 'blue')} Upload Speed: {colored(f'{upload_speed:.1f} Mbps', 'cyan')}")
            ping_color = "green" if ping < 50 else "yellow" if ping < 100 else "red"
            print(f"  {colored('•', 'blue')} Ping: {colored(f'{ping:.1f} ms', ping_color)}")

    except Exception as e:
        print(colored(f"Error fetching health information: {e}", "red"))


def edit_job_command(
    job_id: str,
    command: str | None = None,
    priority: int | None = None,
    num_gpus: int | None = None,
    bypass_confirm: bool = False,
    target_name: str | None = None,
) -> None:
    try:
        job = api_client.get_job(job_id, target_name=target_name)

        if not job:
            print(colored(f"Job {job_id} not found", "red"))
            return

        if job["status"] != "queued":
            print(colored(f"Only queued jobs can be edited. Job {job_id} has status: {job['status']}", "red"))
            return

        print(f"\n{colored('Current job details:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} ID: {colored(job_id, 'magenta')}")
        print(f"  {colored('•', 'blue')} Command: {colored(job['command'], 'white')}")
        print(f"  {colored('•', 'blue')} Priority: {colored(str(job['priority']), 'cyan')}")
        print(f"  {colored('•', 'blue')} GPUs: {colored(str(job['num_gpus']), 'cyan')}")

        print(f"\n{colored('Will edit to:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} Command: {colored(command if command is not None else 'unchanged', 'white')}")
        print(
            f"  {colored('•', 'blue')} Priority: {colored(str(priority) if priority is not None else 'unchanged', 'cyan')}"
        )
        print(
            f"  {colored('•', 'blue')} GPUs: {colored(str(num_gpus) if num_gpus is not None else 'unchanged', 'cyan')}"
        )

        if not utils.confirm_action("Edit this job?", bypass=bypass_confirm):
            print(colored("Operation cancelled.", "yellow"))
            return

        result = api_client.edit_job(job_id, command, priority, num_gpus, target_name=target_name)

        print(colored("\nJob edited successfully:", "green", attrs=["bold"]))
        print(f"  {colored('•', 'green')} ID: {colored(result['id'], 'magenta')}")
        print(f"  {colored('•', 'green')} Command: {colored(result['command'], 'white')}")
        print(f"  {colored('•', 'green')} Priority: {colored(str(result['priority']), 'cyan')}")
        print(f"  {colored('•', 'green')} GPUs: {colored(str(result['num_gpus']), 'cyan')}")

    except Exception as e:
        print(colored(f"\nError editing job: {e}", "red"))
        sys.exit(1)


def get_job_info(job_id: str, target_name: str | None = None) -> None:
    try:
        job = api_client.get_job(job_id, target_name=target_name)

        if not job:
            print(colored(f"Job {job_id} not found", "red"))
            return

        color_map = {"queued": "yellow", "running": "green", "completed": "blue", "failed": "red", "killed": "red"}
        status_color = color_map.get(job["status"], "white")

        def format_time(ts) -> str:
            return utils.format_timestamp(ts) if ts else "N/A"

        runtime = utils.calculate_runtime(job)
        runtime_str = utils.format_runtime(runtime) if runtime else "N/A"

        print(f"\n{colored('Job Details:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} ID: {colored(job_id, 'magenta')}")
        status_color_typed: utils.Color = (
            "yellow"
            if status_color == "yellow"
            else "green"
            if status_color == "green"
            else "blue"
            if status_color == "blue"
            else "red"
            if status_color == "red"
            else "white"
        )
        print(f"  {colored('•', 'blue')} Status: {colored(job['status'].upper(), status_color_typed)}")

        print(f"\n{colored('Command:', 'blue', attrs=['bold'])}")
        print(f"  {colored(job['command'], 'white')}")

        print(f"\n{colored('User & Scheduling:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} User: {colored(job['user'], 'cyan')}")
        print(f"  {colored('•', 'blue')} Priority: {colored(str(job['priority']), 'cyan')}")
        print(f"  {colored('•', 'blue')} Node: {colored(job['node_name'], 'cyan')}")
        print(f"  {colored('•', 'blue')} Created: {colored(format_time(job.get('created_at')), 'cyan')}")

        print(f"\n{colored('GPU Information:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} Number of GPUs: {colored(str(job['num_gpus']), 'cyan')}")
        if job["gpu_idxs"]:
            print(f"  {colored('•', 'blue')} GPU Indices: {colored(', '.join(map(str, job['gpu_idxs'])), 'cyan')}")
        if job.get("ignore_blacklist"):
            print(f"  {colored('•', 'blue')} Blacklist: {colored('Ignored', 'yellow')}")

        print(f"\n{colored('Git Information:', 'blue', attrs=['bold'])}")
        print(f"  {colored('•', 'blue')} Repository: {colored(job['git_repo_url'], 'cyan')}")
        print(f"  {colored('•', 'blue')} Branch: {colored(job['git_branch'], 'cyan')}")
        print(f"  {colored('•', 'blue')} Tag: {colored(job['git_tag'], 'cyan')}")

        if job["status"] in ["running", "completed", "failed", "killed"]:
            print(f"\n{colored('Execution Information:', 'blue', attrs=['bold'])}")
            print(f"  {colored('•', 'blue')} Started: {colored(format_time(job.get('started_at')), 'cyan')}")

            if job.get("screen_session_name"):
                print(f"  {colored('•', 'blue')} Screen Session: {colored(job['screen_session_name'], 'cyan')}")

            if job.get("pid"):
                print(f"  {colored('•', 'blue')} Process ID: {colored(str(job['pid']), 'cyan')}")

            if job["status"] in ["completed", "failed", "killed"]:
                print(f"  {colored('•', 'blue')} Completed: {colored(format_time(job.get('completed_at')), 'cyan')}")
                print(f"  {colored('•', 'blue')} Runtime: {colored(runtime_str, 'cyan')}")
                if job.get("exit_code") is not None:
                    exit_code_color = "green" if job.get("exit_code") == 0 else "red"
                    print(f"  {colored('•', 'blue')} Exit Code: {colored(str(job.get('exit_code')), exit_code_color)}")
                if job.get("error_message"):
                    print(f"  {colored('•', 'blue')} Error: {colored(job['error_message'], 'red')}")

        if job.get("integrations"):
            print(f"\n{colored('Integrations:', 'blue', attrs=['bold'])}")
            for integration in job["integrations"]:
                print(f"  {colored('•', 'blue')} {integration}")
                if integration == "wandb" and job.get("wandb_url"):
                    print(f"    - URL: {colored(job['wandb_url'], 'yellow')}")

        if job.get("notifications"):
            print(f"\n{colored('Notifications:', 'blue', attrs=['bold'])}")
            for notification in job["notifications"]:
                print(f"  {colored('•', 'blue')} {notification}")
                if job.get("notification_messages") and notification in job.get("notification_messages", {}):
                    print(f"    - Last Message: {job['notification_messages'][notification]}")

        print(f"\n{colored('Actions:', 'blue', attrs=['bold'])}")
        if job["status"] == "queued":
            print(f"  {colored('•', 'blue')} View in Queue: {colored('nx queue', 'green')}")
            print(f"  {colored('•', 'blue')} Edit Job: {colored(f'nx edit {job_id}', 'green')}")
            print(f"  {colored('•', 'blue')} Remove Job: {colored(f'nx remove {job_id}', 'green')}")
        elif job["status"] == "running":
            print(f"  {colored('•', 'blue')} View Logs: {colored(f'nx logs {job_id}', 'green')}")
            print(f"  {colored('•', 'blue')} Attach to Screen: {colored(f'nx attach {job_id}', 'green')}")
            print(f"  {colored('•', 'blue')} Kill Job: {colored(f'nx kill {job_id}', 'green')}")
        else:  # completed, failed, killed
            print(f"  {colored('•', 'blue')} View Logs: {colored(f'nx logs {job_id}', 'green')}")
            print(f"  {colored('•', 'blue')} View History: {colored('nx history', 'green')}")

    except Exception as e:
        print(colored(f"Error getting job information: {e}", "red"))


def handle_blacklist(args, target_name: str | None = None) -> None:
    try:
        gpu_idxs = utils.parse_gpu_list(args.gpus)
        gpus = api_client.get_gpus(target_name=target_name)

        valid_idxs = {gpu["index"] for gpu in gpus}
        invalid_idxs = [idx for idx in gpu_idxs if idx not in valid_idxs]
        if invalid_idxs:
            print(colored(f"Invalid GPU idxs: {', '.join(map(str, invalid_idxs))}", "red"))
            return

        result = api_client.manage_blacklist(gpu_idxs, args.blacklist_action, target_name=target_name)

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


def print_status(target_name: str | None = None) -> None:
    try:
        import importlib.metadata

        if not api_client.check_api_connection(target_name=target_name):
            active_target_name, target_cfg = config.get_active_target(target_name)
            if target_cfg:
                error_msg = f"Cannot connect to target '{active_target_name}' ({target_cfg.host}:{target_cfg.port})"
            else:
                error_msg = "Cannot connect to local server (localhost:54323)"
            raise RuntimeError(error_msg)

        try:
            VERSION = importlib.metadata.version("nexusai")
        except importlib.metadata.PackageNotFoundError:
            VERSION = "unknown"

        status = api_client.get_server_status(target_name=target_name)

        server_version = status.get("server_version", "unknown")
        if server_version != VERSION:
            print(
                colored(
                    f"WARNING: Nexus client version ({VERSION}) does not match "
                    f"Nexus server version ({server_version}).",
                    "yellow",
                )
            )

        health = api_client.get_detailed_health(refresh=False, target_name=target_name)
        if health.get("status") == "unhealthy":
            utils.print_health_warning()

        node_name = status.get("node_name", "unknown")
        completed = status.get("completed_jobs", 0)

        gpus = api_client.get_gpus(target_name=target_name)
        running_jobs = api_client.get_jobs(status="running", target_name=target_name)
        queued_jobs = api_client.get_jobs(status="queued", target_name=target_name)

        print(f"Node: {colored(node_name, 'cyan')}\n")

        available_gpus_list = [
            str(g["index"])
            for g in gpus
            if not g.get("running_job_id") and not g.get("is_blacklisted") and g.get("process_count", 0) == 0
        ]
        in_use_gpus = [str(g["index"]) for g in gpus if g.get("running_job_id")]
        external_gpus = [
            str(g["index"])
            for g in gpus
            if not g.get("running_job_id") and not g.get("is_blacklisted") and g.get("process_count", 0) > 0
        ]
        blacklisted_gpus_list = [str(g["index"]) for g in gpus if g.get("is_blacklisted")]

        gpu_status_parts = []
        if available_gpus_list:
            count = len(available_gpus_list)
            gpu_status_parts.append(f"{count} available {colored('[' + ', '.join(available_gpus_list) + ']', 'green')}")
        if in_use_gpus:
            count = len(in_use_gpus)
            gpu_status_parts.append(f"{count} in use {colored('[' + ', '.join(in_use_gpus) + ']', 'cyan')}")
        if external_gpus:
            count = len(external_gpus)
            gpu_status_parts.append(f"{count} external {colored('[' + ', '.join(external_gpus) + ']', 'yellow')}")
        if blacklisted_gpus_list:
            count = len(blacklisted_gpus_list)
            gpu_status_parts.append(
                f"{count} blacklisted {colored('[' + ', '.join(blacklisted_gpus_list) + ']', 'red')}"
            )

        if gpu_status_parts:
            print(f"{colored('GPUs:', 'white', attrs=['bold'])} {' | '.join(gpu_status_parts)}\n")
        else:
            print(f"{colored('GPUs:', 'white', attrs=['bold'])} {colored('None', 'yellow')}\n")

        if running_jobs:
            print(colored(f"Running Jobs ({len(running_jobs)}):", "white", attrs=["bold"]))
            for job in sorted(running_jobs, key=lambda j: j.get("started_at", 0)):
                runtime = utils.calculate_runtime(job)
                runtime_str = utils.format_runtime(runtime)
                utils.format_timestamp(job.get("started_at"))

                if job.get("num_gpus", 0) == 0:
                    resource_str = colored("CPU", "cyan")
                else:
                    gpu_idxs = job.get("gpu_idxs", [])
                    if gpu_idxs:
                        gpu_list = ",".join(map(str, gpu_idxs))
                        resource_str = colored(f"GPU{'s' if len(gpu_idxs) > 1 else ''}: {gpu_list}", "cyan")
                    else:
                        resource_str = colored(
                            f"{job.get('num_gpus')} GPU{'s' if job.get('num_gpus', 0) > 1 else ''}", "cyan"
                        )

                command = job.get("command", "")
                if len(command) > 80:
                    command = command[:77] + "..."

                print(
                    f"  {colored('•', 'white')} {colored(job['id'], 'magenta')} ({resource_str}) - {colored(runtime_str, 'cyan')}"
                )
                print(f"    {colored(command, 'white', attrs=['bold'])}")
                if job.get("wandb_url"):
                    print(f"    W&B: {colored(job['wandb_url'], 'yellow')}")
                else:
                    print()
            print()

        if queued_jobs:
            preview_count = min(3, len(queued_jobs))
            print(
                colored(
                    f"Queue ({len(queued_jobs)} job{'s' if len(queued_jobs) != 1 else ''} waiting):",
                    "white",
                    attrs=["bold"],
                )
            )
            for idx, job in enumerate(
                sorted(queued_jobs, key=lambda j: (-j.get("priority", 0), j.get("created_at", 0)))[:preview_count], 1
            ):
                if job.get("num_gpus", 0) == 0:
                    resource_str = "CPU"
                else:
                    gpu_count = job.get("num_gpus", 1)
                    resource_str = f"{gpu_count} GPU{'s' if gpu_count > 1 else ''}"

                priority = job.get("priority", 0)
                command = job.get("command", "")
                if len(command) > 60:
                    command = command[:57] + "..."

                print(f"  {idx}. {colored(job['id'], 'magenta')} ({resource_str}, Priority: {priority}) - {command}")
            print()

        print(f"History: {colored(str(completed), 'blue')} job{'s' if completed != 1 else ''} completed")

    except Exception as e:
        print(colored(f"Error: {e}", "red"))


def attach_to_job(cfg: config.NexusCliConfig, target: str | None = None, target_name: str | None = None) -> None:
    try:
        user = cfg.user or "anonymous"

        if target is None:
            running_jobs = api_client.get_jobs("running", target_name=target_name)
            user_jobs = [j for j in running_jobs if j.get("user") == user]

            if not user_jobs:
                print(colored(f"No running jobs found for user '{user}'", "yellow"))
                return

            valid_jobs = [j for j in user_jobs if j.get("started_at") is not None]
            if not valid_jobs:
                print(colored(f"No running jobs with valid start times found for user '{user}'", "yellow"))
                return

            valid_jobs.sort(key=lambda x: x.get("started_at", 0), reverse=True)
            target = valid_jobs[0]["id"]
            print(colored(f"Attaching to most recent job: {target}", "blue"))
        elif target.isdigit():
            gpu_idx = int(target)
            gpus = api_client.get_gpus(target_name=target_name)
            gmatch = next((g for g in gpus if g["index"] == gpu_idx), None)
            if not gmatch:
                print(colored(f"No GPU found with index {gpu_idx}", "red"))
                return
            job_id = gmatch.get("running_job_id")
            if not job_id:
                print(colored(f"No running job found on GPU {gpu_idx}", "yellow"))
                return
            target = job_id

        if target is None:
            print(colored("No job target specified", "red"))
            return

        job = api_client.get_job(target, target_name=target_name)
        if not job:
            print(colored(f"Job {target} not found", "red"))
            return

        if job["status"] != "running":
            print(colored(f"Cannot attach to job with status: {job['status']}. Job must be running.", "red"))
            return

        screen_session_name = job.get("screen_session_name")
        if not screen_session_name:
            print(colored(f"No screen session found for job {target}", "red"))
            return

        print(colored(f"Attaching to job {target} screen session '{screen_session_name}'", "blue"))
        print(
            "\n"
            + colored("### PRESS CTRL+A, THEN D TO DISCONNECT FROM SCREEN SESSION ###", "yellow", attrs=["bold"])
            + "\n"
        )
        time.sleep(2)

        job_id = job["id"]
        starting_status = job["status"]

        active_target_name, target_cfg = config.get_active_target(target_name)
        if target_cfg is not None:
            import subprocess

            import os as os_module

            env = os_module.environ.copy()
            env["TERM"] = "xterm-256color"

            exit_code = subprocess.call(
                [
                    "ssh",
                    "-t",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    f"{target_cfg.ssh_user}@{target_cfg.host}",
                    "screen",
                    "-r",
                    screen_session_name,
                ],
                env=env,
            )

            if exit_code != 0:
                print(colored(f"\nSSH attach failed (exit {exit_code})", "red"))
                print(colored(f"View logs: nx logs {job_id}", "yellow"))
                return
        else:
            current_user_exit_code = os.system(f"screen -r {screen_session_name}")

            if current_user_exit_code != 0:
                exit_code = os.system(f"sudo -u nexus screen -r {screen_session_name}")

                if exit_code != 0:
                    print(colored("Screen session not found. Available sessions:", "yellow"))
                    os.system("screen -ls")
                    print(colored("\nTroubleshooting tips:", "yellow"))
                    print("  1. Verify that the job is still running and the session name is correct.")
                    print("  2. Check if you have the proper permissions to access the screen session.")
                    print(f"  3. You can always view job logs with: nx logs {job_id}")
                    return

        try:
            updated_job = api_client.get_job(job_id, target_name=target_name)
            if updated_job:
                if updated_job["status"] != starting_status:
                    status_color = "green" if updated_job["status"] == "completed" else "red"
                    print(colored(f"\nJob {job_id} has {updated_job['status']}. Displaying logs:", status_color))

                    if updated_job.get("exit_code") is not None:
                        exit_code_color = "green" if updated_job["exit_code"] == 0 else "red"
                        print(colored(f"Exit code: {updated_job['exit_code']}", exit_code_color))
                else:
                    print(colored("\nRecent logs:", "blue"))

                runtime = utils.calculate_runtime(updated_job)
                runtime_str = utils.format_runtime(runtime) if runtime else "N/A"
                print(colored(f"Runtime: {runtime_str}", "cyan"))

                logs = api_client.get_job_logs(job_id, last_n_lines=1000, target_name=target_name) or ""
                if logs:
                    print("\n" + logs)
                else:
                    print(colored("No logs available", "yellow"))
        except Exception as e:
            print(colored(f"Error retrieving job logs: {e}", "red"))

    except Exception as e:
        print(colored(f"Error attaching to job: {e}", "red"))
