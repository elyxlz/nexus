import asyncio
import dataclasses as dc
import datetime as dt
import hashlib
import os
import pathlib as pl
import subprocess
import time

import base58

from nexus.service.core import logger, models


def generate_job_id() -> str:
    """Generate a unique job ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def get_job_session_name(job_id: str) -> str:
    """Get the screen session name for a job"""
    return f"nexus_job_{job_id}"


def create_job(command: str, git_repo_url: str, git_tag: str, user: str | None, discord_id: str | None) -> models.Job:
    """Create a new job with the given command and git info"""
    job_id = generate_job_id()

    return models.Job(
        id=job_id,
        command=command.strip(),
        status="queued",
        created_at=dt.datetime.now().timestamp(),
        user=user,
        discord_id=discord_id,
        git_repo_url=git_repo_url,
        git_tag=git_tag,
        dir=None,
        started_at=None,
        completed_at=None,
        gpu_index=None,
        exit_code=None,
        error_message=None,
        wandb_url=None,
        marked_for_kill=False,
    )


def build_job_env(gpu_index: int, _env: dict[str, str]) -> dict[str, str]:
    base_env = os.environ.copy()
    return {**base_env, "CUDA_VISIBLE_DEVICES": str(gpu_index), **_env}


async def async_start_job(
    logger: logger.NexusServiceLogger,
    job: models.Job,
    gpu_index: int,
    jobs_dir: pl.Path,
    github_token: str | None,
    job_env: dict[str, str],
) -> models.Job:
    session_name = get_job_session_name(job.id)
    job_dir = jobs_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_file = job_dir / "output.log"
    job_repo_dir = job_dir / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)

    env = build_job_env(gpu_index, _env=job_env)
    # Setup Git credentials if available.
    if github_token:
        askpass_path = job_dir / "askpass.sh"
        askpass_script = f'#!/usr/bin/env bash\necho "{github_token}"\n'
        askpass_path.write_text(askpass_script)
        askpass_path.chmod(0o700)
    else:
        askpass_path = None

    # Build the shell script to run the job.
    script_lines = [
        "#!/bin/bash",
        "set -e",
        "export GIT_TERMINAL_PROMPT=0",
    ]
    if askpass_path:
        script_lines.append(f'export GIT_ASKPASS="{askpass_path}"')
    script_lines.extend(
        [
            'script -f -q -c "',
            f"git clone --depth 1 --single-branch --no-tags --branch {job.git_tag} --quiet '{job.git_repo_url}' '{job_repo_dir}'",
            f"cd '{job_repo_dir}'",
            f"{job.command}",
            f'" "{log_file}"',
        ]
    )

    script_path = job_dir / "run.sh"
    script_path.write_text("\n".join(script_lines))
    script_path.chmod(0o755)

    try:
        # Launch the job using an asynchronous subprocess call.
        await asyncio.create_subprocess_exec("screen", "-dmS", session_name, str(script_path), env=env)
        # Optionally, wait briefly to ensure the process is spawned.
        await asyncio.sleep(0.1)

        # Update the job status (assuming the job is now running in background).
        return dc.replace(job, started_at=dt.datetime.now().timestamp(), gpu_index=gpu_index, status="running")
    except Exception as e:
        logger.error(f"Failed to start job {job.id}: {e}")
        return dc.replace(job, status="failed", error_message=str(e), completed_at=dt.datetime.now().timestamp())


def is_job_session_running(job_id: str) -> bool:
    """Check if a job's screen session is still running"""
    session_name = get_job_session_name(job_id)
    try:
        output = subprocess.check_output(["screen", "-ls", session_name], stderr=subprocess.DEVNULL, text=True)
        return session_name in output
    except subprocess.CalledProcessError:
        return False


def end_job(logger: logger.NexusServiceLogger, _job: models.Job, killed: bool) -> models.Job:
    """Check if a job has completed and update its status. Returns new job instance."""
    if is_job_session_running(_job.id):
        return _job

    job_log = get_job_logs(_job.dir)
    exit_code = get_job_exit_code(logger, job_id=_job.id, job_dir=_job.dir)

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


def get_job_exit_code(logger: logger.NexusServiceLogger, job_id: str, job_dir: pl.Path | None) -> int | None:
    if job_dir is None:
        return None

    try:
        content = get_job_logs(job_dir, last_n_lines=1)
        if content is None:
            raise ValueError("No output log found")
        last_line = content.strip()
        if "COMMAND_EXIT_CODE=" in last_line:
            exit_code_str = last_line.split('COMMAND_EXIT_CODE="')[1].split('"')[0]
            return int(exit_code_str)
        raise ValueError("Could not determine exit code")
    except Exception:
        logger.exception("Error determining exit code for job %s", job_id)
        return None


def get_job_logs(job_dir: pl.Path | None, last_n_lines: int | None = None) -> str | None:
    if job_dir is None:
        return None

    logs = job_dir / "output.log"
    if not logs.exists():
        return None

    if last_n_lines is None:
        return logs.read_text()
    else:
        with logs.open() as f:
            return "".join(f.readlines()[-last_n_lines:])


def kill_job_session(logger: logger.NexusServiceLogger, job_id: str) -> None:
    """Kill a job session"""
    try:
        subprocess.run(f"pkill -f {job_id}", shell=True)
    except Exception as e:
        logger.error(f"failed to kill all processes for job {job_id}: {e}")
