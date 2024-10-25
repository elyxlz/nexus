import dataclasses as dc
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import typing
from enum import Enum
from pathlib import Path

import base58
import humanize
import toml
from termcolor import colored


class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dc.dataclass
class Job:
    id: str
    command: str
    status: JobStatus
    created_at: float
    started_at: float | None
    completed_at: float | None
    gpu_index: int | None
    screen_session: str | None
    env_vars: list[tuple[str, str]]
    exit_code: int | None
    error_message: str | None
    log_dir: Path | None


@dc.dataclass
class Config:
    log_dir: Path
    refresh_rate: int
    history_limit: int


@dc.dataclass
class NexusState:
    jobs: list[Job]
    blacklisted_gpus: list[int]
    is_paused: bool
    last_updated: float


@dc.dataclass
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool = False


# Type alias for termcolor attributes
TermColorAttr = typing.Literal[
    "bold", "dark", "underline", "blink", "reverse", "concealed", "strike"
]


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
    nexus logs service       View or follow service logs
    nexus attach <id|gpu>    Attach to running job's screen session
    nexus attach service     Attach to the nexus service session
    nexus blacklist          Show blacklisted GPUs
    nexus blacklist add      Add GPU to blacklist
    nexus blacklist remove   Remove GPU from blacklist
    nexus config             View current config
    nexus config edit        Edit config.toml in $EDITOR
    nexus help               Show this help
    nexus help <command>     Show detailed help for command""")


def print_command_help(command: str) -> None:
    help_text = {
        "add": f"{colored('nexus add \"command\"', 'green')}\nAdd a new job to the queue. Enclose command in quotes.",
        "kill": f"{colored('nexus kill <id|gpu>', 'green')}\nKill a running job by its ID or GPU number.",
        "attach": f"{colored('nexus attach <id|gpu>', 'green')}\nAttach to a running job's screen session. Use Ctrl+A+D to detach.",
        "blacklist": f"{colored('nexus blacklist', 'green')}\nManage GPU blacklist:\n  nexus blacklist         Show blacklisted GPUs\n  nexus blacklist add    Add GPU to blacklist\n  nexus blacklist remove Remove GPU from blacklist",
        "config": f"{colored('Configuration:', 'blue', attrs=['bold'])}\n{colored('nexus config', 'green')}\nView current configuration.\n{colored('nexus config edit', 'green')}\nEdit configuration in $EDITOR.",
    }
    print(
        help_text.get(
            command, colored(f"No detailed help available for: {command}", "red")
        )
    )


def attach_screen_session(session: str) -> None:
    try:
        subprocess.run(["screen", "-r", session], check=True)
    except subprocess.CalledProcessError as e:
        print(colored(f"Failed to attach to session: {e}", "red"))


def load_config() -> Config:
    home = Path.home()
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

    log_dir = Path(
        os.path.expanduser(config_data.get("paths", {}).get("log_dir", "~/.nexus/logs"))
    )
    refresh_rate = config_data.get("display", {}).get("refresh_rate", 5)
    history_limit = config_data.get("history", {}).get("limit", 1000)

    # Ensure directories exist
    log_dir.mkdir(parents=True, exist_ok=True)

    return Config(log_dir, refresh_rate, history_limit)


def generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:3]
    return base58.b58encode(hash_bytes).decode()


def create_default_state() -> NexusState:
    return NexusState(
        jobs=[], blacklisted_gpus=[], is_paused=False, last_updated=time.time()
    )


def load_state(config: Config) -> NexusState:
    state_path = config.log_dir / "state.json"

    try:
        if not state_path.exists():
            return create_default_state()

        with open(state_path) as f:
            data = json.load(f)
            # Convert JobStatus strings back to enum
            for job in data["jobs"]:
                job["status"] = JobStatus(job["status"])
                if job.get("log_dir"):
                    job["log_dir"] = Path(job["log_dir"])
            state = NexusState(**data)

            # Convert dict jobs back to Job objects
            state.jobs = [Job(**job) for job in data["jobs"]]

            # Clean up old jobs
            clean_completed_jobs(state, config)
            return state

    except (json.JSONDecodeError, KeyError, TypeError):
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
        return create_default_state()


def save_state(state: NexusState, config: Config) -> None:
    state_path = config.log_dir / "state.json"
    temp_path = state_path.with_suffix(".json.tmp")

    state.last_updated = time.time()

    try:
        state_dict = dc.asdict(state)
        for job in state_dict["jobs"]:
            job["status"] = job["status"].value
            if job.get("log_dir"):
                job["log_dir"] = str(job["log_dir"])

        with open(temp_path, "w") as f:
            json.dump(state_dict, f, indent=2)

        temp_path.replace(state_path)

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def create_job(command: str, config: Config) -> Job:
    job_id = generate_job_id()
    log_dir = config.log_dir / "jobs" / job_id

    return Job(
        id=job_id,
        command=command.strip(),
        status=JobStatus.QUEUED,
        created_at=time.time(),
        started_at=None,
        completed_at=None,
        gpu_index=None,
        screen_session=None,
        env_vars=[],
        exit_code=None,
        error_message=None,
        log_dir=log_dir,
    )


def start_job(job: Job, gpu_index: int, state: NexusState, config: Config) -> None:
    session_name = f"nexus_job_{job.id}"
    assert job.log_dir is not None

    job.log_dir.mkdir(parents=True, exist_ok=True)

    # Prepare environment variables
    env = os.environ.copy()
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": str(gpu_index),
            "NEXUS_JOB_ID": job.id,
            "NEXUS_GPU_ID": str(gpu_index),
            "NEXUS_START_TIME": str(time.time()),
        }
    )

    # Remove problematic screen variables
    env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

    stdout_log = job.log_dir / "stdout.log"
    stderr_log = job.log_dir / "stderr.log"

    script_path = job.log_dir / "run.sh"
    script_content = f"""#!/bin/bash
exec 1> "{stdout_log}" 2> "{stderr_log}"
{job.command}
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    try:
        subprocess.run(
            ["screen", "-dmS", session_name, str(script_path)], env=env, check=True
        )

        job.started_at = time.time()
        job.gpu_index = gpu_index
        job.screen_session = session_name
        job.status = JobStatus.RUNNING
        job.env_vars = list(env.items())

        log_service_event(config, f"Job {job.id} started on GPU {gpu_index}")
        save_state(state, config)
    except subprocess.CalledProcessError as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        job.completed_at = time.time()
        log_service_event(config, f"Failed to start job {job.id}: {e}")
        save_state(state, config)
        raise


def is_job_alive(job: Job) -> bool:
    if not job.screen_session:
        return False

    try:
        output = subprocess.check_output(
            ["screen", "-ls", job.screen_session], stderr=subprocess.DEVNULL, text=True
        )
        return job.screen_session in output
    except subprocess.CalledProcessError:
        return False


def get_gpu_info(config: Config, state: NexusState) -> list[GpuInfo]:
    if os.environ.get("NEXUS_DEV"):
        return [
            GpuInfo(0, "Mock GPU 0", 8192, 2048),
            GpuInfo(1, "Mock GPU 1", 16384, 4096),
        ]

    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
    except subprocess.CalledProcessError as e:
        log_service_event(config, f"Failed to get GPU info: {e}")
        return []

    gpus = []
    for line in output.strip().split("\n"):
        try:
            index, name, total, used = [x.strip() for x in line.split(",")]
            gpu = GpuInfo(
                index=int(index),
                name=name,
                memory_total=int(float(total)),
                memory_used=int(float(used)),
                is_blacklisted=(int(index) in state.blacklisted_gpus),
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as e:
            log_service_event(config, f"Error parsing GPU info: {e}")
            continue

    return gpus


def clean_completed_jobs(state: NexusState, config: Config) -> None:
    completed = [
        j for j in state.jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    if len(completed) > config.history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[: config.history_limit]
        active_jobs = [
            j for j in state.jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        ]
        state.jobs = active_jobs + keep_jobs


def log_service_event(config: Config, message: str) -> None:
    log_path = config.log_dir / "service.log"
    timestamp = dt.datetime.now(dt.timezone.utc)
    try:
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(colored(f"Failed to write to service log: {e}", "red"))


def start_service(config: Config) -> None:
    session_name = "nexus"
    dummy_job = Job(
        "",
        "",
        JobStatus.RUNNING,
        0,
        None,
        None,
        None,
        session_name,
        [],
        None,
        None,
        None,
    )

    if not is_job_alive(dummy_job):
        service_log = config.log_dir / "service.log"
        command = f"exec 1> {service_log} 2>&1; python {__file__} service"

        try:
            subprocess.run(
                ["screen", "-dmS", session_name, "bash", "-c", command], check=True
            )
            print(colored("Nexus service started", "green"))
            log_service_event(config, "Nexus service started")
        except subprocess.CalledProcessError as e:
            print(colored(f"Failed to start service: {e}", "red"))
    else:
        print(colored("Nexus service is already running", "yellow"))


def nexus_service(config: Config) -> None:
    state = load_state(config)
    log_service_event(config, "Service starting")

    while True:
        try:
            gpus = get_gpu_info(config, state)

            # Update job statuses
            for job in state.jobs:
                if job.status == JobStatus.RUNNING:
                    if not is_job_alive(job):
                        job.status = JobStatus.COMPLETED
                        job.completed_at = time.time()
                        log_service_event(config, f"Job {job.id} completed")
                        save_state(state, config)

            # Find available non-blacklisted GPUs
            available_gpus = [
                g
                for g in gpus
                if not g.is_blacklisted
                and not any(
                    j.status == JobStatus.RUNNING and j.gpu_index == g.index
                    for j in state.jobs
                )
            ]

            # Start new jobs if queue is not paused
            if not state.is_paused:
                for gpu in available_gpus:
                    queued_jobs = [
                        j for j in state.jobs if j.status == JobStatus.QUEUED
                    ]
                    if queued_jobs:
                        job = queued_jobs[0]
                        try:
                            start_job(job, gpu.index, state, config)
                        except Exception as e:
                            log_service_event(
                                config, f"Failed to start job {job.id}: {e}"
                            )
                            job.status = JobStatus.FAILED
                            job.completed_at = time.time()
                            job.error_message = str(e)
                            save_state(state, config)

            time.sleep(config.refresh_rate)

        except Exception as e:
            log_service_event(config, f"Service error: {e}")
            time.sleep(config.refresh_rate)


def handle_status(config: Config) -> None:
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


def main():
    try:
        config = load_config()
        args = sys.argv[1:]

        if not args:
            start_service(config)
            handle_status(config)
            return

        command = args[0]
        state = load_state(config)

        if command == "service":
            nexus_service(config)

        elif command == "stop":
            try:
                subprocess.run(["screen", "-S", "nexus", "-X", "quit"], check=True)
                print(colored("Nexus service stopped", "green"))
                log_service_event(config, "Nexus service stopped")
            except subprocess.CalledProcessError:
                print(colored("Failed to stop service", "red"))

        elif command == "restart":
            try:
                subprocess.run(["screen", "-S", "nexus", "-X", "quit"], check=True)
                time.sleep(1)
                start_service(config)
            except subprocess.CalledProcessError:
                print(colored("Failed to restart service", "red"))

        elif command == "add":
            if len(args) < 2:
                print(colored('Usage: nexus add "command"', "red"))
                return
            command_str = " ".join(args[1:])
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

        elif command == "queue":
            queued_jobs = [j for j in state.jobs if j.status == JobStatus.QUEUED]
            print(colored("Pending Jobs:", "blue", attrs=["bold"]))
            for pos, job in enumerate(queued_jobs, 1):
                print(
                    f"{colored(str(pos), 'blue')}. {colored(job.id, 'magenta')} - {colored(job.command, 'white')}"
                )

        elif command == "history":
            completed_jobs = [
                j
                for j in state.jobs
                if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
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

        elif command == "kill":
            if len(args) < 2:
                print(colored("Usage: nexus kill <id|gpu>", "red"))
                return

            target = args[1]
            killed = False

            try:
                # Try as GPU index
                gpu_index = int(target)
                for job in state.jobs:
                    if job.status == JobStatus.RUNNING and job.gpu_index == gpu_index:
                        assert job.screen_session is not None
                        attach_screen_session(job.screen_session)
                        job.status = JobStatus.FAILED
                        job.completed_at = time.time()
                        job.error_message = "Killed by user"
                        print(
                            f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')} "
                            f"{colored(f'on GPU {gpu_index}', 'yellow')}"
                        )
                        killed = True
                        break
            except ValueError:
                # Try as job ID
                for job in state.jobs:
                    if job.id == target and job.screen_session:
                        subprocess.run(
                            ["screen", "-S", job.screen_session, "-X", "quit"],
                            check=True,
                        )
                        job.status = JobStatus.FAILED
                        job.completed_at = time.time()
                        job.error_message = "Killed by user"
                        print(
                            f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')}"
                        )
                        killed = True
                        break

            if not killed:
                print(colored(f"No running job found with ID or GPU: {target}", "red"))
            else:
                save_state(state, config)

        elif command == "remove":
            if len(args) < 2:
                print(colored("Usage: nexus remove <id>", "red"))
                return

            job_id = args[1]
            original_len = len(state.jobs)
            state.jobs = [
                j
                for j in state.jobs
                if not (j.id == job_id and j.status == JobStatus.QUEUED)
            ]

            if len(state.jobs) != original_len:
                print(f"{colored('Removed job', 'green')} {colored(job_id, 'magenta')}")
                save_state(state, config)
            else:
                print(colored(f"No queued job found with ID: {job_id}", "red"))

        elif command == "pause":
            state.is_paused = True
            save_state(state, config)
            print(colored("Queue processing paused", "yellow"))

        elif command == "resume":
            state.is_paused = False
            save_state(state, config)
            print(colored("Queue processing resumed", "green"))

        elif command == "logs":
            if len(args) < 2:
                print(colored("Usage: nexus logs <id|service>", "red"))
                return

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

        elif command == "attach":
            if len(args) < 2:
                print(colored("Usage: nexus attach <id|gpu|service>", "red"))
                return

            target = args[1]
            session_name = "nexus"

            if target == "service":
                dummy_job = Job(
                    "",
                    "",
                    JobStatus.RUNNING,
                    0,
                    None,
                    None,
                    None,
                    session_name,
                    [],
                    None,
                    None,
                    None,
                )
                if is_job_alive(dummy_job):
                    subprocess.run(["screen", "-r", session_name])
                else:
                    print(colored("No running nexus service found.", "red"))
            else:
                try:
                    gpu_index = int(target)
                    running_job = next(
                        (
                            j
                            for j in state.jobs
                            if j.status == JobStatus.RUNNING
                            and j.gpu_index == gpu_index
                        ),
                        None,
                    )
                    if running_job and running_job.screen_session:
                        subprocess.run(["screen", "-r", running_job.screen_session])
                    else:
                        print(
                            colored(f"No running job found on GPU {gpu_index}", "red")
                        )
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

        elif command == "blacklist":
            if len(args) > 1:
                subcommand = args[1]
                if subcommand == "add":
                    if len(args) < 3:
                        print(
                            colored("Usage: nexus blacklist add <idx[,idx...]>", "red")
                        )
                        return
                    indices = [int(x) for x in args[2].split(",")]
                    state.blacklisted_gpus.extend(indices)
                    state.blacklisted_gpus = list(set(state.blacklisted_gpus))  # dedupe
                    save_state(state, config)
                    print(colored(f"Added GPUs to blacklist: {indices}", "green"))

                elif subcommand == "remove":
                    if len(args) < 3:
                        print(
                            colored(
                                "Usage: nexus blacklist remove <idx[,idx...]>", "red"
                            )
                        )
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

        elif command == "config":
            if len(args) > 1 and args[1] == "edit":
                editor = os.environ.get("EDITOR", "vim")
                config_path = Path.home() / ".nexus/config.toml"
                subprocess.run([editor, str(config_path)])
            else:
                config_path = Path.home() / ".nexus/config.toml"
                print(
                    f"{colored('Current configuration', 'blue', attrs=['bold'])}:\n{config_path.read_text()}"
                )

        elif command == "help":
            if len(args) > 1:
                print_command_help(args[1])
            else:
                print_help()

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
