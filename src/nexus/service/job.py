import datetime as dt
import hashlib
import os
import pathlib
import subprocess
import time

import base58

from nexus.service import models
from nexus.service.logger import logger
from nexus.service.git import clone_repository, cleanup_repo


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


def create_job(
    command: str,
    repo_url: str,
    git_tag: str,
) -> models.Job:
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
        repo_url=repo_url,
        git_tag=git_tag,
    )


def get_job_session_name(job_id: str) -> str:
    return f"nexus_job_{job_id}"


def get_job_repo_dir(repo_dir: pathlib.Path, job_id: str) -> pathlib.Path:
    return repo_dir / job_id


def start_job(job: models.Job, gpu_index: int, log_dir: pathlib.Path, repo_dir: pathlib.Path, env_file: pathlib.Path) -> models.Job:
    """Start a job on a specific GPU"""
    session_name = get_job_session_name(job.id)

    # Setup logging directory
    job_log_dir = log_dir / "jobs" / job.id
    job_log_dir.mkdir(parents=True, exist_ok=True)
    combined_log = job_log_dir / "output.log"

    job_dir = get_job_repo_dir(repo_dir, job_id=job.id)
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        clone_repository(job.repo_url, tag=job.git_tag, target_dir=job_dir)

        env = os.environ.copy()
        env.update({"CUDA_VISIBLE_DEVICES": str(gpu_index)})
        env.update(parse_env_file(env_file))
        env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

        script_path = job_log_dir / "run.sh"
        script_content = f"""#!/bin/bash
cd "{job_dir}"
script -f -q -c "{job.command}" "{combined_log}"
"""
        script_path.write_text(script_content)
        script_path.chmod(0o755)
        subprocess.run(["screen", "-dmS", session_name, str(script_path)], env=env, check=True)

        job.started_at = dt.datetime.now().timestamp()
        job.gpu_index = gpu_index
        job.status = "running"

    except (subprocess.CalledProcessError, RuntimeError) as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = dt.datetime.now().timestamp()
        cleanup_repo(job_dir)
        logger.error(f"Failed to start job {job.id}: {e}")
        raise

    return job


def get_job_logs(job: models.Job, log_dir: pathlib.Path) -> str | None:
    """Get combined logs for a job"""
    job_log_dir = log_dir / "jobs" / job.id

    if not job_log_dir:
        return None

    combined_log = job_log_dir / "output.log"
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


def kill_job(job: models.Job, repo_dir: pathlib.Path) -> None:
    """Kill a running job"""
    session_name = get_job_session_name(job.id)
    try:
        subprocess.run(["screen", "-S", session_name, "-X", "quit"], check=True)
        job.status = "failed"
        job.completed_at = dt.datetime.now().timestamp()
        job.error_message = "Killed by user"
        cleanup_repo(get_job_repo_dir(repo_dir, job_id=job.id))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to kill job: {e}")
