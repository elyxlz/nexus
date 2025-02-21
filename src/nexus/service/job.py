import dataclasses as dc
import datetime as dt
import hashlib
import os
import pathlib as pl
import subprocess
import time

import base58

from nexus.service import logger, models


def generate_job_id() -> str:
    """Generate a unique job ID using timestamp and random bytes"""
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def parse_env_file(env_file: pl.Path) -> dict[str, str]:
    """Parse environment file and return new environment dict"""
    env = {}
    if env_file.exists():
        with env_file.open() as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    env[key] = value
    return env


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
        started_at=None,
        completed_at=None,
        gpu_index=None,
        exit_code=None,
        error_message=None,
        wandb_url=None,
        marked_for_kill=False,
    )


def build_job_env(gpu_index: int, env_file: pl.Path) -> dict[str, str]:
    base_env = os.environ.copy()
    file_env = parse_env_file(env_file)
    env = {**base_env, "CUDA_VISIBLE_DEVICES": str(gpu_index), **file_env}
    return env


def start_job(job: models.Job, gpu_index: int, jobs_dir: pl.Path, env_file: pl.Path) -> models.Job:
    """Start a job on a specific GPU. Returns new job instance."""
    session_name = get_job_session_name(job.id)
    job_dir = jobs_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    log = job_dir / "output.log"
    job_repo_dir = job_dir / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)

    env = build_job_env(gpu_index, env_file=env_file)
    github_token = env.get("GITHUB_TOKEN")

    # Setup files
    if github_token:
        askpass_path = job_dir / "askpass.sh"
        askpass_script = f"""#!/usr/bin/env bash
echo "{github_token}"
"""
        askpass_path.write_text(askpass_script)
        askpass_path.chmod(0o700)
    else:
        askpass_path = None

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
            f'" "{log}"',
        ]
    )

    script_path = job_dir / "run.sh"
    script_content = "\n".join(script_lines)
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    try:
        subprocess.run(["screen", "-dmS", session_name, str(script_path)], env=env, check=True)
        return dc.replace(job, started_at=dt.datetime.now().timestamp(), gpu_index=gpu_index, status="running")
    except subprocess.CalledProcessError as e:
        logger.logger.error(f"Failed to start job {job.id}: {e}")
        return dc.replace(job, status="failed", error_message=str(e), completed_at=dt.datetime.now().timestamp())


def is_job_session_running(job_id: str) -> bool:
    """Check if a job's screen session is still running"""
    session_name = get_job_session_name(job_id)
    try:
        output = subprocess.check_output(["screen", "-ls", session_name], stderr=subprocess.DEVNULL, text=True)
        return session_name in output
    except subprocess.CalledProcessError:
        return False


def end_job(job: models.Job, jobs_dir: pl.Path, killed: bool) -> models.Job:
    """Check if a job has completed and update its status. Returns new job instance."""
    if is_job_session_running(job.id):
        return job

    job_log = get_job_logs(job.id, jobs_dir=jobs_dir)
    exit_code = get_job_exit_code(job.id, jobs_dir=jobs_dir)

    if killed:
        new_job = dc.replace(
            job, status="failed", error_message="Killed by user", completed_at=dt.datetime.now().timestamp()
        )
    elif job_log is None:
        new_job = dc.replace(
            job, status="failed", error_message="No output log found", completed_at=dt.datetime.now().timestamp()
        )
    elif exit_code is None:
        new_job = dc.replace(
            job,
            status="failed",
            error_message="Could not find exit code in log",
            completed_at=dt.datetime.now().timestamp(),
        )
    else:
        new_job = dc.replace(
            job,
            exit_code=exit_code,
            status="completed" if exit_code == 0 else "failed",
            error_message=None if exit_code == 0 else f"Job failed with exit code {exit_code}",
            completed_at=dt.datetime.now().timestamp(),
        )

    return new_job


def get_job_exit_code(job_id: str, jobs_dir: pl.Path) -> int | None:
    try:
        content = get_job_logs(job_id, jobs_dir, last_n_lines=1)
        if content is None:
            raise ValueError("No output log found")
        last_line = content.strip()
        if "COMMAND_EXIT_CODE=" in last_line:
            exit_code_str = last_line.split('COMMAND_EXIT_CODE="')[1].split('"')[0]
            return int(exit_code_str)
        raise ValueError("Could not determine exit code")
    except Exception:
        logger.logger.exception("Error determining exit code for job %s", job_id)
        return None


def get_job_logs(job_id: str, jobs_dir: pl.Path, last_n_lines: int | None = None) -> str | None:
    """Get job logs"""
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
    """Kill a job session"""
    try:
        subprocess.run(f"pkill -f {job_id}", shell=True)
    except Exception as e:
        logger.logger.error(f"failed to kill all processes for job {job_id}: {e}")
