import datetime as dt
import os
import pathlib
import subprocess
import sys
import time

import humanize
import toml
from termcolor import colored

from nexus.main import create_default_state
from nexus.models import Config, JobStatus
from nexus.service import create_job, nexus_service, start_service
from nexus.utils import (
    get_gpu_info,
    load_state,
    log_service_event,
    save_state,
)


def load_config() -> Config:
    home = pathlib.Path.home()
    base_dir = home / ".nexus"
    config_path = base_dir / "config.toml"

    # Create default config if it doesn't exist
    if not config_path.exists():
        default_config = """[paths]
log_dir = "~/.nexus/logs"

[display]
refresh_rate = 10  # Status view refresh in seconds

[history]
limit = 1000  # Number of completed jobs to keep

[gpu]
blacklist = []  # list of GPU indices to exclude
"""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(default_config)

    # Read and parse config
    content = config_path.read_text()
    config_data = toml.loads(content)

    log_dir = pathlib.Path(
        os.path.expanduser(config_data.get("paths", {}).get("log_dir", "~/.nexus/logs"))
    )
    refresh_rate = config_data.get("display", {}).get("refresh_rate", 5)
    history_limit = config_data.get("history", {}).get("limit", 1000)

    # Ensure directories exist
    log_dir.mkdir(parents=True, exist_ok=True)

    return Config(log_dir, refresh_rate, history_limit)


def print_help() -> None:
    print(f"""{colored('Nexus: GPU Job Management CLI', 'green', attrs=['bold'])}

{colored('USAGE', 'blue', attrs=['bold'])}:
    nexus                     Show status
    nexus stop               Stop the nexus service
    nexus restart            Restart the nexus service
    nexus add "command"      Add job to queue
    nexus queue              Show pending jobs
    nexus history            Show completed jobs
    nexus kill <id|gpu>      Kill job by ID or GPU number
    nexus remove <id>        Remove job from queue
    nexus pause              Pause queue processing
    nexus resume             Resume queue processing
    nexus logs <id>          View logs for job
    nexus logs service       View service logs
    nexus attach <id|gpu>    Attach to running job's screen session
    nexus attach service     Attach to service session
    nexus blacklist          Show blacklisted GPUs
    nexus config             View current config
    nexus config edit        Edit config.toml in $EDITOR
    nexus help               Show this help
    nexus help <command>     Show help for command""")


def print_command_help(command: str) -> None:
    help_text = {
        "add": f"{colored('nexus add \"command\"', 'green')}\nAdd a new job to the queue. Enclose command in quotes.",
        "kill": f"{colored('nexus kill <id|gpu>', 'green')}\nKill a running job by its ID or GPU number.",
        "attach": f"{colored('nexus attach <id|gpu>', 'green')}\nAttach to a running job's screen session. Use Ctrl+A+D to detach.",
        "blacklist": f"{colored('nexus blacklist', 'green')}\nManage GPU blacklist:\n  nexus blacklist         Show blacklisted GPUs\n  nexus blacklist add    Add GPU to blacklist\n  nexus blacklist remove Remove GPU from blacklist",
        "config": f"{colored('Configuration:', 'blue', attrs=['bold'])}\n{colored('nexus config', 'green')}\nView current configuration.\n{colored('nexus config edit', 'green')}\nEdit configuration in $EDITOR.",
        "logs": f"{colored('nexus logs <id|service>', 'green')}\nView logs for a job or the service. Use 'service' to view service logs.",
        "queue": f"{colored('nexus queue', 'green')}\nShow all pending jobs in the queue.",
        "history": f"{colored('nexus history', 'green')}\nShow history of completed jobs.",
        "remove": f"{colored('nexus remove <id>', 'green')}\nRemove a pending job from the queue by its ID.",
        "pause": f"{colored('nexus pause', 'green')}\nPause processing of the job queue.",
        "resume": f"{colored('nexus resume', 'green')}\nResume processing of the job queue.",
        "stop": f"{colored('nexus stop', 'green')}\nStop the nexus service.",
        "restart": f"{colored('nexus restart', 'green')}\nRestart the nexus service.",
    }
    print(
        help_text.get(
            command, colored(f"No detailed help available for: {command}", "red")
        )
    )


def handle_status(config: Config, args: list[str]) -> None:
    state = load_state(config)
    gpus = get_gpu_info(config, state)

    queued_count = sum(1 for j in state.jobs if j.status == JobStatus.QUEUED)
    completed_count = sum(
        1 for j in state.jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    )

    queue_status = (
        colored("PAUSED", "yellow") if state.is_paused else colored("RUNNING", "green")
    )

    print(
        f"{colored('Queue', 'blue', attrs=['bold'])}: {queued_count} jobs pending [{queue_status}]"
    )
    print(
        f"{colored('History', 'blue', attrs=['bold'])}: {completed_count} jobs completed\n"
    )

    print(f"{colored('GPUs', 'white', attrs=['bold'])}:")
    for gpu in gpus:
        mem_usage = (gpu.memory_used / gpu.memory_total) * 100
        status = colored("BLACKLISTED", "yellow") if gpu.is_blacklisted else ""
        print(
            f"GPU {colored(str(gpu.index), 'white')} ({gpu.name}, "
            f"{gpu.memory_used}MB/{gpu.memory_total}MB, {mem_usage:.0f}%) {status}:"
        )

        running_job = next(
            (
                j
                for j in state.jobs
                if j.status == JobStatus.RUNNING and j.gpu_index == gpu.index
            ),
            None,
        )

        if running_job:
            runtime = (
                time.time() - running_job.started_at if running_job.started_at else 0
            )
            start_time = (
                dt.datetime.fromtimestamp(running_job.started_at)
                if running_job.started_at
                else "Unknown"
            )

            print(f"  {colored('Job ID', 'magenta')}: {running_job.id}")
            print(
                f"  {colored('Command', 'white', attrs=['bold'])}: {running_job.command}"
            )
            print(
                f"  {colored('Runtime', 'cyan')}: {colored(humanize.naturaldelta(runtime), 'cyan')}"
            )
            print(f"  {colored('Started', 'cyan')}: {colored(start_time, 'cyan')}")
        elif not gpu.is_blacklisted:
            print(f"  {colored('Available', 'green', attrs=['bold'])}")


def handle_service(config: Config) -> None:
    nexus_service(config)


def handle_stop(config: Config) -> None:
    try:
        subprocess.run(["screen", "-S", "nexus", "-X", "quit"], check=True)
        print(colored("Nexus service stopped", "green"))
        log_service_event(config, "Nexus service stopped")
    except subprocess.CalledProcessError:
        print(colored("Failed to stop service", "red"))


def handle_restart(config: Config) -> None:
    try:
        subprocess.run(["screen", "-S", "nexus", "-X", "quit"], check=True)
        time.sleep(1)
        start_service(config)
    except subprocess.CalledProcessError:
        print(colored("Failed to restart service", "red"))


def handle_add(config: Config, args: list[str]) -> None:
    if len(args) < 2:
        print(colored('Usage: nexus add "command"', "red"))
        return
    command_str = " ".join(args[1:])
    state = load_state(config)
    job = create_job(command_str, config)

    print(
        f"{colored('Added job', 'green')} {colored(job.id, 'magenta', attrs=['bold'])}"
    )
    print(
        f"{colored('Command', 'white', attrs=['bold'])}: {colored(job.command, 'cyan')}"
    )
    print(
        f"{colored('Time Added', 'white', attrs=['bold'])}: {colored(dt.datetime.fromtimestamp(job.created_at), 'cyan')}"
    )
    print(colored("The job has been added to the queue.", "green"))

    log_service_event(config, f"Job {job.id} added to queue: {job.command}")
    state.jobs.append(job)
    save_state(state, config)


def handle_queue(config: Config) -> None:
    state = load_state(config)
    queued_jobs = [j for j in state.jobs if j.status == JobStatus.QUEUED]
    print(colored("Pending Jobs:", "blue", attrs=["bold"]))
    for pos, job in enumerate(queued_jobs, 1):
        print(
            f"{colored(str(pos), 'blue')}. {colored(job.id, 'magenta')} - {colored(job.command, 'white')}"
        )


def handle_history(config: Config) -> None:
    state = load_state(config)
    completed_jobs = [
        j for j in state.jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    completed_jobs.sort(key=lambda x: x.completed_at or 0, reverse=True)

    print(colored("Completed Jobs:", "blue", attrs=["bold"]))
    for job in completed_jobs:
        runtime = (job.completed_at or 0) - (job.started_at or 0)
        status_color = "red" if job.status == JobStatus.FAILED else "green"
        status_text = colored(job.status.name, status_color)
        gpu_str = str(job.gpu_index) if job.gpu_index is not None else "Unknown"

        print(
            f"{colored(job.id, 'magenta')}: {colored(job.command, 'white')} "
            f"(Status: {status_text}, "
            f"Runtime: {colored(humanize.naturaldelta(runtime), 'cyan')}, "
            f"GPU: {colored(gpu_str, 'yellow')})"
        )
        if job.error_message:
            print(f"  Error: {colored(job.error_message, 'red')}")


def handle_kill(config: Config, args: list[str]) -> None:
    if len(args) < 2:
        print(colored("Usage: nexus kill <id|gpu>", "red"))
        return

    state = load_state(config)
    target = args[1]
    killed = False

    try:
        # Try as GPU index
        gpu_index = int(target)
        for job in state.jobs:
            if job.status == JobStatus.RUNNING and job.gpu_index == gpu_index:
                assert job.screen_session is not None
                subprocess.run(
                    ["screen", "-S", job.screen_session, "-X", "quit"], check=True
                )
                job.status = JobStatus.FAILED
                job.completed_at = time.time()
                job.error_message = "Killed by user"
                print(
                    f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')} {colored(f'on GPU {gpu_index}', 'yellow')}"
                )
                killed = True
                break
    except ValueError:
        # Try as job ID
        for job in state.jobs:
            if job.id == target and job.screen_session:
                subprocess.run(
                    ["screen", "-S", job.screen_session, "-X", "quit"], check=True
                )
                job.status = JobStatus.FAILED
                job.completed_at = time.time()
                job.error_message = "Killed by user"
                print(f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')}")
                killed = True
                break

    if not killed:
        print(colored(f"No running job found with ID or GPU: {target}", "red"))
    else:
        save_state(state, config)


def handle_remove(config: Config, args: list[str]) -> None:
    if len(args) < 2:
        print(colored("Usage: nexus remove <id>", "red"))
        return

    state = load_state(config)
    job_id = args[1]
    original_len = len(state.jobs)
    state.jobs = [
        j for j in state.jobs if not (j.id == job_id and j.status == JobStatus.QUEUED)
    ]

    if len(state.jobs) != original_len:
        print(f"{colored('Removed job', 'green')} {colored(job_id, 'magenta')}")
        save_state(state, config)
    else:
        print(colored(f"No queued job found with ID: {job_id}", "red"))


def handle_pause(config: Config) -> None:
    state = load_state(config)
    state.is_paused = True
    save_state(state, config)
    print(colored("Queue processing paused", "yellow"))


def handle_resume(config: Config) -> None:
    state = load_state(config)
    state.is_paused = False
    save_state(state, config)
    print(colored("Queue processing resumed", "green"))


def handle_logs(config: Config, args: list[str]) -> None:
    if len(args) < 2:
        print(colored("Usage: nexus logs <id|service>", "red"))
        return

    state = load_state(config)
    if args[1] == "service":
        log_path = config.log_dir / "service.log"
        if log_path.exists():
            print(log_path.read_text())
        else:
            print(colored("No service log found.", "red"))
    else:
        job = next((j for j in state.jobs if j.id == args[1]), None)
        if job and job.log_dir:
            print(colored("=== STDOUT ===", "blue", attrs=["bold"]))
            stdout_path = job.log_dir / "stdout.log"
            if stdout_path.exists():
                print(stdout_path.read_text())

            print(f"\n{colored('=== STDERR ===', 'red', attrs=['bold'])}")
            stderr_path = job.log_dir / "stderr.log"
            if stderr_path.exists():
                print(stderr_path.read_text())
        else:
            print(colored(f"No logs found for job {args[1]}", "red"))


def handle_attach(config: Config, args: list[str]) -> None:
    if len(args) < 2:
        print(colored("Usage: nexus attach <id|gpu|service>", "red"))
        return

    state = load_state(config)
    target = args[1]

    if target == "service":
        subprocess.run(["screen", "-r", "nexus"])
    else:
        try:
            # Try as GPU index
            gpu_index = int(target)
            running_job = next(
                (
                    j
                    for j in state.jobs
                    if j.status == JobStatus.RUNNING and j.gpu_index == gpu_index
                ),
                None,
            )
            if running_job and running_job.screen_session:
                subprocess.run(["screen", "-r", running_job.screen_session])
            else:
                print(colored(f"No running job found on GPU {gpu_index}", "red"))
        except ValueError:
            # Try as job ID
            job = next(
                (
                    j
                    for j in state.jobs
                    if j.id == target and j.status == JobStatus.RUNNING
                ),
                None,
            )
            if job and job.screen_session:
                subprocess.run(["screen", "-r", job.screen_session])
            else:
                print(colored(f"No running job found with ID: {target}", "red"))


def handle_blacklist(config: Config, args: list[str]) -> None:
    state = load_state(config)
    if len(args) > 1:
        subcommand = args[1]
        if subcommand == "add":
            if len(args) < 3:
                print(colored("Usage: nexus blacklist add <idx[,idx...]>", "red"))
                return
            indices = [int(x) for x in args[2].split(",")]
            state.blacklisted_gpus.extend(indices)
            state.blacklisted_gpus = list(set(state.blacklisted_gpus))  # dedupe
            save_state(state, config)
            print(colored(f"Added GPUs to blacklist: {indices}", "green"))

        elif subcommand == "remove":
            if len(args) < 3:
                print(colored("Usage: nexus blacklist remove <idx[,idx...]>", "red"))
                return

            indices = [int(x) for x in args[2].split(",")]
            state.blacklisted_gpus = [
                x for x in state.blacklisted_gpus if x not in indices
            ]
            save_state(state, config)
            print(colored(f"Removed GPUs from blacklist: {indices}", "green"))
        else:
            print(colored(f"Unknown blacklist subcommand: {subcommand}", "red"))
    else:
        if state.blacklisted_gpus:
            print(colored("Blacklisted GPUs:", "blue", attrs=["bold"]))
            gpus = get_gpu_info(config, state)
            for idx in state.blacklisted_gpus:
                gpu = next((g for g in gpus if g.index == idx), None)
                if gpu:
                    print(f"GPU {colored(str(idx), 'yellow')}: {gpu.name}")
        else:
            print(colored("No GPUs are blacklisted", "green"))


def handle_config(args: list[str]) -> None:
    if len(args) > 1 and args[1] == "edit":
        editor = os.environ.get("EDITOR", "vim")
        config_path = pathlib.Path.home() / ".nexus/config.toml"
        subprocess.run([editor, str(config_path)])
    else:
        config_path = pathlib.Path.home() / ".nexus/config.toml"
        print(
            f"{colored('Current configuration', 'blue', attrs=['bold'])}:\n{config_path.read_text()}"
        )


def handle_help(args: list[str]) -> None:
    if len(args) > 1:
        print_command_help(args[1])
    else:
        print_help()


def main():
    try:
        config = load_config()
        args = sys.argv[1:]

        if not args:
            start_service(config)
            handle_status(config, args=args)
            return

        command = args[0]

        # Command router
        handlers = {
            "service": lambda: handle_service(config),
            "stop": lambda: handle_stop(config),
            "restart": lambda: handle_restart(config),
            "add": lambda: handle_add(config, args=args),
            "queue": lambda: handle_queue(config),
            "history": lambda: handle_history(config),
            "kill": lambda: handle_kill(config, args=args),
            "remove": lambda: handle_remove(config, args=args),
            "pause": lambda: handle_pause(config),
            "resume": lambda: handle_resume(config),
            "logs": lambda: handle_logs(config, args=args),
            "attach": lambda: handle_attach(config, args=args),
            "blacklist": lambda: handle_blacklist(config, args=args),
            "config": lambda: handle_config(args),
            "help": lambda: handle_help(args),
        }

        if command in handlers:
            handlers[command]()
        else:
            print(colored(f"Unknown command: {command}", "red"))
            print_help()

    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(colored(f"Error: {str(e)}", "red"))
        sys.exit(1)


if __name__ == "__main__":
    main()
