import datetime as dt
import hashlib
import os
import pathlib
import subprocess
import time

import base58

from nexus.service import models
from nexus.service.logger import logger


def generate_job_id() -> str:
    """Generate a unique job ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def create_job(command: str, working_dir: pathlib.Path) -> models.Job:
    """Create a new job with the given command"""
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
        working_dir=working_dir,
    )


def get_job_session_name(job_id: str) -> str:
    return f"nexus_job_{job_id}"


def start_job(job: models.Job, gpu_index: int, log_dir: pathlib.Path) -> models.Job:
    """Start a job on a specific GPU"""
    session_name = get_job_session_name(job.id)

    job_log_dir = log_dir / "jobs" / job.id
    job_log_dir.mkdir(parents=True, exist_ok=True)

    combined_log = job_log_dir / "output.log"

    # Prepare environment variables
    env = os.environ.copy()
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": str(gpu_index),
            "NEXUS_JOB_ID": job.id,
            "NEXUS_GPU_ID": str(gpu_index),
            "NEXUS_START_TIME": str(dt.datetime.now().timestamp()),
        }
    )

    # Remove problematic screen variables
    env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

    # Create a minimal script that just changes directory and runs the command
    script_path = job_log_dir / "run.sh"
    script_content = f"""#!/bin/bash
cd "{job.working_dir}"
script -f -q -c "{job.command}" "{combined_log}"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    try:
        subprocess.run(["screen", "-dmS", session_name, str(script_path)], env=env, check=True)

        job.started_at = dt.datetime.now().timestamp()
        job.gpu_index = gpu_index
        job.status = "running"

    except subprocess.CalledProcessError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = dt.datetime.now().timestamp()
        logger.info(f"Failed to start job {job.id}: {e}")
        raise

    return job


def get_job_logs(job: models.Job, log_dir: pathlib.Path) -> str | None:
    """Get combined logs for a job"""
    job_log_dir = log_dir / "jobs" / job.id

    if not job_log_dir:
        return None

    combined_log = job_log_dir / "output.log"

    # Return the same content for both stdout and stderr since they're combined
    output = combined_log.read_text() if combined_log.exists() else None
    return output


def is_job_running(job: models.Job) -> bool:
    """Check if a job's screen session is still running"""
    session_name = get_job_session_name(job.id)

    try:
        output = subprocess.check_output(["screen", "-ls", session_name], stderr=subprocess.DEVNULL, text=True)
        return session_name in output
    except subprocess.CalledProcessError:
        return False


def kill_job(job: models.Job) -> None:
    """Kill a running job"""
    session_name = get_job_session_name(job.id)
    try:
        subprocess.run(["screen", "-S", session_name, "-X", "quit"], check=True)
        job.status = "failed"
        job.completed_at = dt.datetime.now().timestamp()
        job.error_message = "Killed by user"
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to kill job: {e}")
