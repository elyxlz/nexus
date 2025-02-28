import asyncio
import dataclasses as dc
import datetime as dt
import hashlib
import os
import pathlib as pl
import signal
import subprocess
import time

import base58

from nexus.service.core import exceptions as exc
from nexus.service.core import logger, schemas

__all__ = [
    "generate_job_id",
    "get_job_session_name",
    "create_job",
    "build_job_env",
    "async_start_job",
    "is_job_running",
    "async_end_job",
    "get_job_exit_code",
    "async_get_job_logs",
    "kill_job",
]


def generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def get_job_session_name(job_id: str) -> str:
    return f"nexus_job_{job_id}"


def create_job(
    command: str,
    git_repo_url: str,
    git_tag: str,
    git_branch: str,
    user: str,
    node_name: str,
    env: dict[str, str] = {},
    jobrc: str | None = None,
    search_wandb: bool = False,
    notifications: list[schemas.NotificationType] = [],
) -> schemas.Job:
    return schemas.Job(
        id=generate_job_id(),
        command=command.strip(),
        status="queued",
        created_at=dt.datetime.now().timestamp(),
        user=user,
        git_repo_url=git_repo_url,
        git_tag=git_tag,
        git_branch=git_branch,
        node_name=node_name,
        dir=None,
        started_at=None,
        completed_at=None,
        gpu_index=None,
        exit_code=None,
        error_message=None,
        wandb_url=None,
        marked_for_kill=False,
        pid=None,
        notification_messages={},
        env=env,
        jobrc=jobrc,
        search_wandb=search_wandb,
        notifications=notifications,
    )


def build_job_env(gpu_index: int, _env: dict[str, str]) -> dict[str, str]:
    base_env = os.environ.copy()
    return {**base_env, "CUDA_VISIBLE_DEVICES": str(gpu_index), **_env}


@exc.handle_exception(PermissionError, exc.JobError, message="Failed to create job directories")
@exc.handle_exception(OSError, exc.JobError, message="Failed to create job directories")
def create_directories(_logger: logger.NexusServiceLogger, dir_path: pl.Path) -> tuple[pl.Path, pl.Path]:
    dir_path.mkdir(parents=True, exist_ok=True)
    log_file = dir_path / "output.log"
    job_repo_dir = dir_path / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)
    return log_file, job_repo_dir


@exc.handle_exception(PermissionError, exc.JobError, message="Failed to create GitHub token helper")
@exc.handle_exception(OSError, exc.JobError, message="Failed to create GitHub token helper")
def _create_git_token_helper(_logger: logger.NexusServiceLogger, dir_path: pl.Path, git_token: str) -> pl.Path:
    askpass_path = dir_path / "askpass.sh"
    askpass_script = f'#!/usr/bin/env bash\necho "{git_token}"\n'
    askpass_path.write_text(askpass_script)
    askpass_path.chmod(0o700)
    return askpass_path


def setup_github_auth(_logger: logger.NexusServiceLogger, dir_path: pl.Path, git_token: str) -> pl.Path | None:
    if not git_token:
        return None

    return _create_git_token_helper(_logger, dir_path, git_token)


def _build_script_content(
    log_file: pl.Path,
    job_repo_dir: pl.Path,
    git_repo_url: str,
    git_tag: str,
    command: str,
    askpass_path: pl.Path | None,
    jobrc: str | None = None,
) -> str:
    script_lines = [
        "#!/bin/bash",
        "set -e",
        "export GIT_TERMINAL_PROMPT=0",
    ]

    if askpass_path:
        script_lines.append(f'export GIT_ASKPASS="{askpass_path}"')

    # Build the script command content
    script_command_lines = [
        f"git clone --depth 1 --single-branch --no-tags --branch {git_tag} --quiet '{git_repo_url}' '{job_repo_dir}'",
        f"cd '{job_repo_dir}'",
    ]

    # Insert pre-job script if provided
    if jobrc:
        script_command_lines.append(jobrc)

    # Add the main command
    script_command_lines.append(command)

    # Wrap with script command
    script_lines.append('script -f -q -c "')
    script_lines.extend(script_command_lines)
    script_lines.append(f'" "{log_file}"')

    return "\n".join(script_lines)


@exc.handle_exception(PermissionError, exc.JobError, message="Failed to create job script")
@exc.handle_exception(OSError, exc.JobError, message="Failed to create job script")
def _write_job_script(_logger: logger.NexusServiceLogger, job_dir: pl.Path, script_content: str) -> pl.Path:
    script_path = job_dir / "run.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)
    return script_path


def create_job_script(
    _logger: logger.NexusServiceLogger,
    job_dir: pl.Path,
    log_file: pl.Path,
    job_repo_dir: pl.Path,
    git_repo_url: str,
    git_tag: str,
    command: str,
    askpass_path: pl.Path | None,
    jobrc: str | None = None,
) -> pl.Path:
    script_content = _build_script_content(log_file, job_repo_dir, git_repo_url, git_tag, command, askpass_path, jobrc)
    return _write_job_script(_logger, job_dir, script_content)


@exc.handle_exception(Exception, exc.JobError, message="Failed to build job.env")
def _build_environment(_logger: logger.NexusServiceLogger, gpu_index: int, job_env: dict[str, str]) -> dict[str, str]:
    return build_job_env(gpu_index, _env=job_env)


@exc.handle_exception_async(FileNotFoundError, exc.JobError, message="Cannot launch job process - file not found")
@exc.handle_exception_async(PermissionError, exc.JobError, message="Cannot launch job process - permission denied")
async def _launch_screen_process(
    _logger: logger.NexusServiceLogger, session_name: str, script_path: str, env: dict[str, str]
) -> int:
    process = await asyncio.create_subprocess_exec(
        "screen",
        "-dmS",
        session_name,
        script_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.wait()  # Wait for screen process to complete

    if process.returncode is not None and process.returncode != 0:
        raise exc.JobError(message=f"Screen process exited with code {process.returncode}")

    # Give screen a moment to start
    await asyncio.sleep(0.2)

    # Get the PID of the actual process inside the screen session
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", f"{session_name}.*{script_path}", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    pids = stdout.decode().strip().split("\n")
    if not pids or not pids[0]:
        _logger.error(f"Could not find PID for job in session {session_name}")
        raise exc.JobError(message=f"Failed to get PID for job in session {session_name}")

    return int(pids[0])  # Return the first PID found (main process)


async def async_start_job(
    _logger: logger.NexusServiceLogger,
    job: schemas.Job,
    gpu_index: int,
) -> schemas.Job:
    # Validate job directory
    if job.dir is None:
        raise exc.JobError(message=f"Job directory not set for job {job.id}")

    # Create directories
    log_file, job_repo_dir = create_directories(_logger, dir_path=job.dir)

    # Use client-provided environment variables
    env = _build_environment(_logger, gpu_index=gpu_index, job_env=job.env)

    # Get GitHub token from environment if available
    git_token = job.env.get("GIT_TOKEN")
    askpass_path = setup_github_auth(_logger, dir_path=job.dir, git_token=git_token) if git_token else None

    # Create the job script
    script_path = create_job_script(
        _logger,
        job_dir=job.dir,
        log_file=log_file,
        job_repo_dir=job_repo_dir,
        git_repo_url=job.git_repo_url,
        git_tag=job.git_tag,
        command=job.command,
        askpass_path=askpass_path,
        jobrc=job.jobrc,
    )

    # Start the job
    session_name = get_job_session_name(job.id)
    try:
        # Launch the job using screen and get the PID
        pid = await _launch_screen_process(_logger, session_name, str(script_path), env)

        # Update the job status with PID
        return dc.replace(job, started_at=dt.datetime.now().timestamp(), gpu_index=gpu_index, status="running", pid=pid)
    except exc.JobError:
        # Re-raise job errors
        raise
    except Exception as e:
        _logger.error(f"Failed to start job {job.id}: {e}")
        return dc.replace(job, status="failed", error_message=str(e), completed_at=dt.datetime.now().timestamp())


@exc.handle_exception(
    subprocess.CalledProcessError, message="Error checking process status", reraise=False, default_return=False
)
def is_job_running(_logger: logger.NexusServiceLogger, job: schemas.Job) -> bool:
    # If there's no PID, fall back to screen session check
    if job.pid is None:
        session_name = get_job_session_name(job.id)
        output = subprocess.check_output(["screen", "-ls"], stderr=subprocess.DEVNULL, text=True)
        return session_name in output

    # Check if process with PID exists
    try:
        # Send signal 0 to check if process exists without affecting it
        os.kill(job.pid, 0)
        return True
    except ProcessLookupError:
        # Process not found
        return False
    except PermissionError:
        # Process exists but we don't have permission (still means it's running)
        return True


async def async_end_job(_logger: logger.NexusServiceLogger, _job: schemas.Job, killed: bool) -> schemas.Job:
    if is_job_running(_logger, _job):
        return _job

    job_log = await async_get_job_logs(_logger, job_dir=_job.dir)
    exit_code = await get_job_exit_code(_logger, job_id=_job.id, job_dir=_job.dir)

    if killed:
        new_job = dc.replace(
            _job, status="failed", error_message="Killed by user", completed_at=dt.datetime.now().timestamp()
        )
    elif job_log is None:
        new_job = dc.replace(
            _job, status="failed", error_message="No output log found", completed_at=dt.datetime.now().timestamp()
        )
    elif exit_code is None:
        new_job = dc.replace(
            _job,
            status="failed",
            error_message="Could not find exit code in log",
            completed_at=dt.datetime.now().timestamp(),
        )
    else:
        new_job = dc.replace(
            _job,
            exit_code=exit_code,
            status="completed" if exit_code == 0 else "failed",
            error_message=None if exit_code == 0 else f"Job failed with exit code {exit_code}",
            completed_at=dt.datetime.now().timestamp(),
        )

    return new_job


@exc.handle_exception(ValueError, message="Invalid exit code format", reraise=False, default_return=None)
def _parse_exit_code(_logger: logger.NexusServiceLogger, last_line: str) -> int:
    if "COMMAND_EXIT_CODE=" not in last_line:
        raise exc.JobError(message="Could not find exit code in log")

    exit_code_str = last_line.split('COMMAND_EXIT_CODE="')[1].split('"')[0]
    return int(exit_code_str)


@exc.handle_exception_async(Exception, message="Error determining exit code", reraise=False, default_return=None)
async def get_job_exit_code(_logger: logger.NexusServiceLogger, job_id: str, job_dir: pl.Path | None) -> int | None:
    if job_dir is None:
        _logger.warning(f"No directory specified for job {job_id}, cannot determine exit code")
        return None

    content = await async_get_job_logs(_logger, job_dir=job_dir, last_n_lines=1)
    if content is None:
        _logger.warning(f"No output log found for job {job_id}")
        raise exc.JobError(f"No output log found for job {job_id}")

    return _parse_exit_code(_logger, content.strip())


@exc.handle_exception(PermissionError, exc.JobError, message="Cannot read job log file")
@exc.handle_exception(OSError, exc.JobError, message="Cannot read job log file")
def _read_log_file(_logger: logger.NexusServiceLogger, log_path: pl.Path, last_n_lines: int | None = None) -> str:
    if last_n_lines is None:
        return log_path.read_text()
    else:
        with log_path.open() as f:
            return "".join(f.readlines()[-last_n_lines:])


async def async_get_job_logs(
    _logger: logger.NexusServiceLogger, job_dir: pl.Path | None, last_n_lines: int | None = None
) -> str | None:
    if job_dir is None:
        return None

    logs = job_dir / "output.log"
    if not logs.exists():
        return None

    return _read_log_file(_logger, logs, last_n_lines)


@exc.handle_exception_async(subprocess.SubprocessError, exc.JobError, message="Failed to kill job processes")
async def kill_job(_logger: logger.NexusServiceLogger, job: schemas.Job) -> None:
    if job.pid is not None:
        try:
            os.kill(job.pid, signal.SIGTERM)

            for _ in range(10):
                try:
                    os.kill(job.pid, 0)
                    await asyncio.sleep(0.1)
                except ProcessLookupError:
                    return

            os.kill(job.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    session_name = get_job_session_name(job.id)
    await asyncio.create_subprocess_exec("screen", "-S", session_name, "-X", "quit")

    await asyncio.create_subprocess_shell(f"pkill -f {job.id}")
