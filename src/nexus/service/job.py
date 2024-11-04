import datetime as dt
import hashlib
import os
import pathlib
import subprocess
import time

import base58

from nexus.service import models
from nexus.service.format import format_job_action
from nexus.service.git import cleanup_repo
from nexus.service.logger import logger


# Utility functions
def generate_job_id() -> str:
    """Generate a unique job ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def parse_env_file(env_file: pathlib.Path) -> dict:
    env = {}
    if env_file.exists():
        with env_file.open() as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    env[key] = value
    return env


def get_job_session_name(job_id: str) -> str:
    return f"nexus_job_{job_id}"


# Core job lifecycle functions
def create_job(command: str, git_repo_url: str, git_tag: str, user: str | None) -> models.Job:
    """Create a new job with the given command and git info"""
    job_id = generate_job_id()

    return models.Job(
        id=job_id,
        command=command.strip(),
        status="queued",
        created_at=dt.datetime.now().timestamp(),
        started_at=None,
        completed_at=None,
        gpu_index=None,
        exit_code=None,
        error_message=None,
        git_repo_url=git_repo_url,
        git_tag=git_tag,
        wandb_url=None,
        user=user,
    )


def start_job(job: models.Job, gpu_index: int, jobs_dir: pathlib.Path, env_file: pathlib.Path) -> models.Job:
    """Start a job on a specific GPU"""
    session_name = get_job_session_name(job.id)

    # Setup logging directory
    job_dir = jobs_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    log = job_dir / "output.log"

    job_repo_dir = job_dir / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)

    try:
        env = os.environ.copy()
        env.update({"CUDA_VISIBLE_DEVICES": str(gpu_index)})
        env.update(parse_env_file(env_file))
        env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

        # Create the job script with git clone and command execution
        script_path = job_dir / "run.sh"
        script_content = f"""#!/bin/bash
set -e  # Exit on error
git clone --depth 1 --single-branch --no-tags --branch {job.git_tag} --quiet {job.git_repo_url} "{job_repo_dir}"
cd "{job_repo_dir}"
script -f -q -c "{job.command}" "{log}"
"""
        script_path.write_text(script_content)
        script_path.chmod(0o755)

        subprocess.run(["screen", "-dmS", session_name, str(script_path)], env=env, check=True)

        job.started_at = dt.datetime.now().timestamp()
        job.gpu_index = gpu_index
        job.status = "running"

    except subprocess.CalledProcessError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = dt.datetime.now().timestamp()
        cleanup_repo(jobs_dir, job_id=job.id)
        logger.info(format_job_action(job, "failed"))
        logger.error(f"Failed to start job {job.id}: {e}")
        raise

    return job


# Job status and monitoring functions
def is_job_running(job: models.Job) -> bool:
    """Check if a job's screen session is still running"""
    session_name = get_job_session_name(job.id)

    try:
        output = subprocess.check_output(["screen", "-ls", session_name], stderr=subprocess.DEVNULL, text=True)
        return session_name in output
    except subprocess.CalledProcessError:
        return False


def update_job_status_if_completed(job: models.Job, jobs_dir: pathlib.Path) -> models.Job:
    """Check if a job has completed and update its status"""
    if is_job_running(job):
        return job

    # Read the output log to get exit code
    output_log = jobs_dir / job.id / "output.log"
    if output_log.exists():
        try:
            content = output_log.read_text()
            # Look for exit code in the last line
            last_line = content.strip().split("\n")[-1]
            if "COMMAND_EXIT_CODE=" in last_line:
                # Extract just the number between the quotes
                exit_code_str = last_line.split('COMMAND_EXIT_CODE="')[1].split('"')[0]
                exit_code = int(exit_code_str)
                job.exit_code = exit_code
                job.status = "completed" if exit_code == 0 else "failed"
                job.error_message = None if exit_code == 0 else f"Job failed with exit code {exit_code}"
            else:
                job.status = "failed"
                job.error_message = "Could not find exit code in log"
        except (ValueError, IOError) as e:
            job.status = "failed"
            job.error_message = f"Failed to read log file: {e}"
    else:
        job.status = "failed"
        job.error_message = "No output log found"

    job.completed_at = dt.datetime.now().timestamp()
    return job


def get_job_logs(job: models.Job, jobs_dir: pathlib.Path) -> str | None:
    """Get logs for a job"""
    job_dir = jobs_dir / job.id

    if not job_dir:
        return None

    logs = job_dir / "output.log"
    output = logs.read_text() if logs.exists() else None
    return output


def kill_job(job: models.Job, jobs_dir: pathlib.Path) -> None:
    """Kill a running job"""
    session_name = get_job_session_name(job.id)
    try:
        subprocess.run(["screen", "-S", session_name, "-X", "quit"], check=True)
        job.status = "failed"
        job.completed_at = dt.datetime.now().timestamp()
        job.error_message = "Killed by user"
        cleanup_repo(jobs_dir=jobs_dir, job_id=job.id)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to kill job: {e}")
