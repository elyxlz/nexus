import hashlib
import os
import pathlib
import subprocess
import time

import base58

from nexus.service.logger import logger
from nexus.service.models import Job


def generate_job_id() -> str:
    """Generate a unique job ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:3]
    return base58.b58encode(hash_bytes).decode()


def create_job(command: str) -> Job:
    """Create a new job with the given command"""
    job_id = generate_job_id()

    return Job(
        id=job_id,
        command=command.strip(),
        status="queued",
        created_at=time.time(),
        started_at=None,
        completed_at=None,
        gpu_index=None,
        screen_session=None,
        exit_code=None,
        error_message=None,
    )


def start_job(job: Job, gpu_index: int, log_dir: pathlib.Path) -> None:
    """Start a job on a specific GPU"""
    session_name = f"nexus_job_{job.id}"

    job_log_dir = log_dir / "jobs" / job.id
    job_log_dir.mkdir(parents=True, exist_ok=True)

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

    stdout_log = job_log_dir / "stdout.log"
    stderr_log = job_log_dir / "stderr.log"

    script_path = job_log_dir / "run.sh"
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
        job.status = "running"

        logger.info(f"Job {job.id} started on GPU {gpu_index}")

    except subprocess.CalledProcessError as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = time.time()
        logger.info(f"Failed to start job {job.id}: {e}")
        raise


def is_job_running(job: Job) -> bool:
    """Check if a job's screen session is still running"""
    if not job.screen_session:
        return False

    try:
        output = subprocess.check_output(
            ["screen", "-ls", job.screen_session], stderr=subprocess.DEVNULL, text=True
        )
        return job.screen_session in output
    except subprocess.CalledProcessError:
        return False


def kill_job(job: Job) -> None:
    """Kill a running job"""
    if job.screen_session:
        try:
            subprocess.run(
                ["screen", "-S", job.screen_session, "-X", "quit"], check=True
            )
            job.status = "failed"
            job.completed_at = time.time()
            job.error_message = "Killed by user"
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to kill job: {e}")


def get_job_logs(job: Job, log_dir: pathlib.Path) -> tuple[str | None, str | None]:
    """Get stdout and stderr logs for a job"""

    job_log_dir = log_dir / "jobs" / job.id

    if not job_log_dir:
        return None, None

    stdout_path = job_log_dir / "stdout.log"
    stderr_path = job_log_dir / "stderr.log"

    stdout = stdout_path.read_text() if stdout_path.exists() else None
    stderr = stderr_path.read_text() if stderr_path.exists() else None

    return stdout, stderr
