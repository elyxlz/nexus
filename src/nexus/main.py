from dataclasses import dataclass
from datetime import datetime
import os
import sys
import time
import hashlib
import base58
import subprocess
import toml
from pathlib import Path
from enum import Enum
from typing import Optional
import humanize
from termcolor import colored


# Data Structures
@dataclass
class Job:
    id: str
    command: str
    start_time: Optional[float]
    gpu_index: Optional[int]
    screen_session: Optional[str]
    status: "JobStatus"
    log_dir: Optional[Path]
    env_vars: list[tuple[str, str]]


@dataclass
class Config:
    log_dir: Path
    jobs_file: Path
    refresh_rate: int
    datetime_format: str
    blacklist: list[int]


@dataclass
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    is_blacklisted: bool = False


class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Config Management
def load_config() -> Config:
    home = Path.home()
    base_dir = home / ".nexus"
    config_path = base_dir / "config.toml"

    # Create default config if it doesn't exist
    if not config_path.exists():
        default_config = """[paths]
log_dir = "~/.nexus/logs"
jobs_file = "~/.nexus/jobs.txt"

[display]
refresh_rate = 10  # Status view refresh in seconds
datetime_format = "%Y-%m-%d %H:%M:%S"

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
    jobs_file = Path(
        os.path.expanduser(
            config_data.get("paths", {}).get("jobs_file", "~/.nexus/jobs.txt")
        )
    )
    refresh_rate = config_data.get("display", {}).get("refresh_rate", 5)
    datetime_format = config_data.get("display", {}).get(
        "datetime_format", "%Y-%m-%d %H:%M:%S"
    )
    blacklist = config_data.get("gpu", {}).get("blacklist", [])

    # Ensure directories exist
    log_dir.mkdir(parents=True, exist_ok=True)
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    if not jobs_file.exists():
        jobs_file.touch()

    return Config(log_dir, jobs_file, refresh_rate, datetime_format, blacklist)


# Job Management
def generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)  # Add randomness to prevent collisions
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:3]
    return base58.b58encode(hash_bytes).decode()


def create_job(command: str, config: Config) -> Job:
    job_id = generate_job_id()
    log_dir = config.log_dir / job_id

    return Job(
        id=job_id,
        command=command.strip(),
        start_time=None,
        gpu_index=None,
        screen_session=None,
        status=JobStatus.QUEUED,
        log_dir=log_dir,
        env_vars=[],
    )


def start_job(job: Job, gpu_index: int, config: Config) -> None:
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
        }
    )

    # Remove problematic screen variables
    env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

    stdout_log = job.log_dir / "stdout.log"
    stderr_log = job.log_dir / "stderr.log"

    # Create a shell script for the job
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

        job.start_time = time.time()
        job.gpu_index = gpu_index
        job.screen_session = session_name
        job.status = JobStatus.RUNNING
        job.env_vars = list(env.items())

        log_service_event(config, f"Job {job.id} started on GPU {gpu_index}")
    except subprocess.CalledProcessError as e:
        job.status = JobStatus.FAILED
        log_service_event(config, f"Failed to start job {job.id}: {e}")
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


# File Operations
def load_jobs(config: Config) -> list[Job]:
    jobs: list[Job] = []

    # Load queued jobs from jobs.txt
    try:
        with open(config.jobs_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    jobs.append(create_job(line, config))
    except Exception as e:
        log_service_event(config, f"Error loading jobs file: {e}")
        return []

    # Load running jobs from screen sessions
    running_jobs = recover_running_jobs(config)
    jobs.extend(running_jobs)

    return jobs


def save_jobs(jobs: list[Job], config: Config) -> None:
    try:
        with open(config.jobs_file, "w") as f:
            for job in jobs:
                if job.status == JobStatus.QUEUED:
                    f.write(f"{job.command}\n")
    except Exception as e:
        log_service_event(config, f"Error saving jobs file: {e}")


# GPU Management
def get_gpu_info(config: Config) -> list[GpuInfo]:
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
                is_blacklisted=(int(index) in config.blacklist),
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as e:
            log_service_event(config, f"Error parsing GPU info: {e}")
            continue

    return gpus


# Recovery
def recover_running_jobs(config: Config) -> list[Job]:
    try:
        screen_output = subprocess.check_output(
            ["screen", "-ls"], stderr=subprocess.DEVNULL, text=True
        )
    except subprocess.CalledProcessError:
        return []

    jobs: list[Job] = []
    for line in screen_output.split("\n"):
        if "nexus_job_" in line:
            try:
                session_name = next(
                    (s for s in line.split() if "nexus_job_" in s), None
                )
                if not session_name:
                    continue

                job_id = session_name.replace("nexus_job_", "")
                pid = line.split(".")[0].strip()

                if not pid.isdigit():
                    continue

                # Try to get GPU index from environment
                try:
                    env_output = subprocess.check_output(
                        ["cat", f"/proc/{pid}/environ"], stderr=subprocess.DEVNULL
                    )
                    env_vars = dict(
                        v.split("=", 1)
                        for v in env_output.decode("utf-8", "ignore").split("\0")
                        if "=" in v
                    )

                    if "CUDA_VISIBLE_DEVICES" in env_vars:
                        gpu_idx = int(env_vars["CUDA_VISIBLE_DEVICES"])
                        job = create_job(
                            "", config
                        )  # Command will be unknown for recovered jobs
                        job.id = job_id
                        job.gpu_index = gpu_idx
                        job.screen_session = session_name
                        job.status = JobStatus.RUNNING
                        job.start_time = float(
                            env_vars.get("NEXUS_START_TIME", time.time())
                        )
                        jobs.append(job)
                except (subprocess.CalledProcessError, ValueError, KeyError):
                    continue

            except Exception as e:
                log_service_event(
                    config, f"Error recovering job from session {line}: {e}"
                )
                continue

    return jobs


# Service Management
def log_service_event(config: Config, message: str) -> None:
    log_path = config.log_dir / "service.log"
    timestamp = datetime.now().strftime(config.datetime_format)
    try:
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(colored(f"Failed to write to service log: {e}", "red"))


def start_service(config: Config) -> None:
    session_name = "nexus"
    if not is_job_alive(
        Job("", "", None, None, session_name, JobStatus.RUNNING, None, [])
    ):
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
    log_service_event(config, "Service starting")

    while True:
        try:
            gpus = get_gpu_info(config)
            jobs = load_jobs(config)

            # Update job statuses
            for job in jobs:
                if job.status == JobStatus.RUNNING:
                    if not is_job_alive(job):
                        job.status = JobStatus.COMPLETED
                        log_service_event(config, f"Job {job.id} completed")

            # Find available non-blacklisted GPUs
            available_gpus = [
                g
                for g in gpus
                if not g.is_blacklisted
                and not any(
                    j.status == JobStatus.RUNNING and j.gpu_index == g.index
                    for j in jobs
                )
            ]

            # Start new jobs if queue is not paused
            if not (config.log_dir / "paused").exists():
                for gpu in available_gpus:
                    queued_jobs = [j for j in jobs if j.status == JobStatus.QUEUED]
                    if queued_jobs:
                        job = queued_jobs[0]
                        try:
                            start_job(job, gpu.index, config)
                        except Exception as e:
                            log_service_event(
                                config, f"Failed to start job {job.id}: {e}"
                            )
                            job.status = JobStatus.FAILED

            save_jobs(jobs, config)

            time.sleep(config.refresh_rate)
        except Exception as e:
            log_service_event(config, f"Service error: {e}")
            time.sleep(config.refresh_rate)


# Command handlers and main function remain largely the same,
# just updated to use the improved helper functions above

if __name__ == "__main__":
    main()


# Command Handlers
def handle_status(config: Config) -> None:
    jobs = load_jobs(config)
    gpus = get_gpu_info(config)

    queued_count = sum(1 for j in jobs if j.status == JobStatus.QUEUED)
    completed_count = sum(1 for j in jobs if j.status == JobStatus.COMPLETED)

    is_paused = (config.log_dir / "paused").exists()
    queue_status = (
        colored("PAUSED", "yellow") if is_paused else colored("RUNNING", "green")
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
        print(
            f"GPU {colored(str(gpu.index), 'white')} ({gpu.name}, {gpu.memory_used}MB/{gpu.memory_total}MB, {mem_usage:.0f}%):"
        )

        running_job = next(
            (
                j
                for j in jobs
                if j.status == JobStatus.RUNNING and j.gpu_index == gpu.index
            ),
            None,
        )

        if running_job:
            runtime = (
                time.time() - running_job.start_time if running_job.start_time else 0
            )
            start_time = (
                datetime.fromtimestamp(running_job.start_time).strftime(
                    config.datetime_format
                )
                if running_job.start_time
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
        else:
            print(f"  {colored('Available', 'green', attrs=['bright'])}")


def print_help():
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
    nexus edit               Open jobs.txt in $EDITOR
    nexus config             View current config
    nexus config edit        Edit config.toml in $EDITOR
    nexus help               Show this help
    nexus help <command>     Show detailed help for command""")


def print_command_help(command: str):
    help_text = {
        "add": f"{colored('nexus add \"command\"', 'green')}\nAdd a new job to the queue. Enclose command in quotes.",
        "kill": f"{colored('nexus kill <id|gpu>', 'green')}\nKill a running job by its ID or GPU number.",
        "attach": f"{colored('nexus attach <id|gpu>', 'green')}\nAttach to a running job's screen session. Use Ctrl+A+D to detach.",
        "config": f"{colored('Configuration:', 'blue', attrs=['bold'])}\n{colored('nexus config', 'green')}\nView current configuration.\n{colored('nexus config edit', 'green')}\nEdit configuration in $EDITOR.",
    }
    print(
        help_text.get(
            command, colored(f"No detailed help available for: {command}", "red")
        )
    )


def main():
    try:
        config = load_config()
        args = sys.argv[1:]

        if not args:
            start_service(config)
            handle_status(config)
            return

        command = args[0]

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
            job = create_job(command_str, config=config)
            added_time = datetime.now().strftime(config.datetime_format)

            print(
                f"{colored('Added job', 'green')} {colored(job.id, 'magenta', attrs=['bold'])}"
            )
            print(
                f"{colored('Command', 'white', attrs=['bold'])}: {colored(job.command, 'cyan')}"
            )
            print(
                f"{colored('Time Added', 'white', attrs=['bold'])}: {colored(added_time, 'cyan')}"
            )
            print(colored("The job has been added to the queue.", "green"))

            log_service_event(config, f"Job {job.id} added to queue: {job.command}")

            jobs = load_jobs(config)
            jobs.append(job)
            save_jobs(jobs, config)

        elif command == "queue":
            jobs = load_jobs(config)
            queued_jobs = [j for j in jobs if j.status == JobStatus.QUEUED]

            print(colored("Pending Jobs:", "blue", attrs=["bold"]))
            for pos, job in enumerate(queued_jobs, 1):
                print(
                    f"{colored(str(pos), 'blue')}. {colored(job.id, 'magenta')} - {colored(job.command, 'white')}"
                )

        elif command == "history":
            jobs = load_jobs(config)
            completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]

            print(colored("Completed Jobs:", "blue", attrs=["bold"]))
            for job in completed_jobs:
                runtime = time.time() - job.start_time if job.start_time else 0
                gpu_str = str(job.gpu_index) if job.gpu_index is not None else "Unknown"
                print(
                    f"{colored(job.id, 'magenta')}: {colored(job.command, 'white')} "
                    f"(Runtime: {colored(humanize.naturaldelta(runtime), 'cyan')}, "
                    f"GPU: {colored(gpu_str, 'yellow')})"
                )

        elif command == "kill":
            if len(args) < 2:
                print(colored("Usage: nexus kill <id|gpu>", "red"))
                return

            target = args[1]
            jobs = load_jobs(config)
            killed = False

            # Try as GPU index
            try:
                gpu_index = int(target)
                for job in jobs:
                    if job.status == JobStatus.RUNNING and job.gpu_index == gpu_index:
                        subprocess.run(
                            ["screen", "-S", job.screen_session, "-X", "quit"],
                            check=True,
                        )
                        job.status = JobStatus.COMPLETED
                        print(
                            f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')} "
                            f"{colored(f'on GPU {gpu_index}', 'yellow')}"
                        )
                        killed = True
                        break
            except ValueError:
                # Try as job ID
                for job in jobs:
                    if job.id == target and job.screen_session:
                        subprocess.run(
                            ["screen", "-S", job.screen_session, "-X", "quit"],
                            check=True,
                        )
                        job.status = JobStatus.COMPLETED
                        print(
                            f"{colored('Killed job', 'green')} {colored(job.id, 'magenta')}"
                        )
                        killed = True
                        break

            if not killed:
                print(colored(f"No running job found with ID or GPU: {target}", "red"))
            else:
                save_jobs(jobs, config)

        elif command == "remove":
            if len(args) < 2:
                print(colored("Usage: nexus remove <id>", "red"))
                return

            job_id = args[1]
            jobs = load_jobs(config)
            jobs = [
                j for j in jobs if not (j.id == job_id and j.status == JobStatus.QUEUED)
            ]
            save_jobs(jobs, config)
            print(f"{colored('Removed job', 'green')} {colored(job_id, 'magenta')}")

        elif command == "pause":
            (config.log_dir / "paused").touch()
            print(colored("Queue processing paused", "yellow"))

        elif command == "resume":
            try:
                (config.log_dir / "paused").unlink()
                print(colored("Queue processing resumed", "green"))
            except FileNotFoundError:
                pass

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
                jobs = load_jobs(config)
                job = next((j for j in jobs if j.id == args[1]), None)
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
                if is_job_alive(session_name):
                    subprocess.run(["screen", "-r", session_name])
                else:
                    print(colored("No running nexus service found.", "red"))
            else:
                try:
                    gpu_index = int(target)
                    session_name = f"nexus_job_gpu_{gpu_index}"
                except ValueError:
                    session_name = f"nexus_job_{target}"

                if is_job_alive(session_name):
                    subprocess.run(["screen", "-r", session_name])
                else:
                    print(colored(f"No running session found for {target}", "red"))

        elif command == "edit":
            editor = os.environ.get("EDITOR", "vim")
            subprocess.run([editor, str(config.jobs_file)])

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
