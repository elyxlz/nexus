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
        started_at=None,
        completed_at=None,
        gpu_index=None,
        exit_code=None,
        error_message=None,
        wandb_url=None,
        marked_for_kill=False,
    )


def start_job(job: models.Job, gpu_index: int, jobs_dir: pathlib.Path, env_file: pathlib.Path) -> models.Job:
    """Start a job on a specific GPU."""
    session_name = get_job_session_name(job.id)
    # Setup logging directory
    job_dir = jobs_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    log = job_dir / "output.log"
    job_repo_dir = job_dir / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({"CUDA_VISIBLE_DEVICES": str(gpu_index)})
    env.update(parse_env_file(env_file))
    github_token = env.get("GITHUB_TOKEN", None)

    # Prepare a GIT_ASKPASS script if a token is available
    askpass_path = None
    if github_token:
        askpass_path = job_dir / "askpass.sh"
        askpass_script = f"""#!/usr/bin/env bash
echo "{github_token}"
"""
        askpass_path.write_text(askpass_script)
        askpass_path.chmod(0o700)

    # Construct the script that will run inside the screen session
    script_path = job_dir / "run.sh"
    script_lines = [
        "#!/bin/bash",
        "set -e",
        # Disable terminal prompt for git
        "export GIT_TERMINAL_PROMPT=0",
    ]

    # If we have a token, set GIT_ASKPASS so that any git operation (including those by pip)
    # can authenticate without modifying home directory or global configuration.
    if askpass_path:
        script_lines.append(f'export GIT_ASKPASS="{askpass_path}"')

    # The main commands to clone the repository and run the job
    script_lines.append('script -f -q -c "')
    script_lines.append(f"git clone --depth 1 --single-branch --no-tags --branch {job.git_tag} --quiet '{job.git_repo_url}' '{job_repo_dir}'")
    script_lines.append(f"cd '{job_repo_dir}'")
    script_lines.append(f"{job.command}")
    script_lines.append(f'" "{log}"')

    script_content = "\n".join(script_lines)
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
        cleanup_repo(jobs_dir, job_id=job.id)
        logger.info(format_job_action(job, "failed"))
        logger.error(f"Failed to start job {job.id}: {e}")
        raise

    return job


# Job status and monitoring functions
def is_job_session_running(job_id: str) -> bool:
    """Check if a job's screen session is still running"""
    session_name = get_job_session_name(job_id)

    try:
        output = subprocess.check_output(["screen", "-ls", session_name], stderr=subprocess.DEVNULL, text=True)
        return session_name in output
    except subprocess.CalledProcessError:
        return False


def end_job(job: models.Job, jobs_dir: pathlib.Path, killed: bool) -> models.Job:
    """Check if a job has completed and update its status"""
    if is_job_session_running(job.id):
        return job

    # Get job logs and exit code
    job_log = get_job_logs(job.id, jobs_dir=jobs_dir)
    exit_code = get_job_exit_code(job.id, jobs_dir=jobs_dir)

    if killed:
        job.status = "failed"
        job.error_message = "Killed by user"
    elif job_log is None:
        job.status = "failed"
        job.error_message = "No output log found"
    elif exit_code is None:
        job.status = "failed"
        job.error_message = "Could not find exit code in log"
    else:
        job.exit_code = exit_code
        job.status = "completed" if exit_code == 0 else "failed"
        job.error_message = None if exit_code == 0 else f"Job failed with exit code {exit_code}"

    job.completed_at = dt.datetime.now().timestamp()
    cleanup_repo(jobs_dir, job_id=job.id)
    return job


def get_job_exit_code(job_id: str, jobs_dir: pathlib.Path) -> int | None:
    """Get the exit code of a job given its job id"""
    content = get_job_logs(job_id, jobs_dir, last_n_lines=1)
    if content is None:
        return None

    try:
        last_line = content.strip()
        if "COMMAND_EXIT_CODE=" in last_line:
            exit_code_str = last_line.split('COMMAND_EXIT_CODE="')[1].split('"')[0]
            return int(exit_code_str)
    except (ValueError, AttributeError):
        pass

    return None


def get_job_logs(job_id: str, jobs_dir: pathlib.Path, last_n_lines: int | None = None) -> str | None:
    job_dir = jobs_dir / job_id

    if not job_dir.exists():
        return None

    logs = job_dir / "output.log"
    if not logs.exists():
        return None

    if last_n_lines is None:
        return logs.read_text()
    else:
        with logs.open() as f:
            return "".join(f.readlines()[-last_n_lines:])


def kill_job_session(job_id: str) -> None:
    session_name = get_job_session_name(job_id)

    # Kill the screen session
    try:
        subprocess.run(["screen", "-S", session_name, "-X", "quit"], check=True)
    except subprocess.CalledProcessError:
        # Session may not exist, ignore
        pass

    # Now ensure all processes under that session are killed
    # We can try to find processes by session name
    try:
        # Find PIDs associated with that job_id (assuming we have run.sh started by screen)
        # For example:
        # pgrep -f can match the command that contains job_id or session_name
        result = subprocess.run(["pgrep", "-f", f"nexus_job_{job_id}"], capture_output=True, text=True)
        pids = [pid.strip() for pid in result.stdout.split("\n") if pid.strip()]
        for pid in pids:
            subprocess.run(["kill", "-9", pid], check=False)
    except Exception as e:
        logger.error(f"Failed to kill all processes for job {job_id}: {e}")
