import asyncio
import dataclasses as dc
import datetime as dt
import hashlib
import os
import pathlib as pl
import re
import shutil
import subprocess
import tempfile
import time

import base58

from nexus.server.core import db
from nexus.server.core import exceptions as exc
from nexus.server.core import schemas
from nexus.server.utils import logger

__all__ = [
    "create_job",
    "async_start_job",
    "prepare_job_environment",
    "is_job_running",
    "async_end_job",
    "async_cleanup_job_repo",
    "async_get_job_logs",
    "kill_job",
    "get_queue",
]


def _generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def _get_job_session_name(job_id: str) -> str:
    return f"nexus_job_{job_id}"


@exc.handle_exception(PermissionError, exc.JobError, message="Failed to create job directories")
@exc.handle_exception(OSError, exc.JobError, message="Failed to create job directories")
def _create_directories(dir_path: pl.Path) -> tuple[pl.Path, pl.Path]:
    dir_path.mkdir(parents=True, exist_ok=True)
    log_file = dir_path / "output.log"
    job_repo_dir = dir_path / "repo"
    job_repo_dir.mkdir(parents=True, exist_ok=True)
    return log_file, job_repo_dir


import pathlib as pl


def _build_script_content(
    log_file: pl.Path,
    job_repo_dir: pl.Path,
    archive_path: pl.Path,
    command: str,
    jobrc: str | None = None,
) -> str:
    jobrc_cmd = f"{jobrc.strip()} && " if jobrc and jobrc.strip() else ""
    return f"""#!/bin/bash
set -e
script -q -e -f -c "mkdir -p {job_repo_dir} && tar -xf {archive_path} -C {job_repo_dir} && cd '{job_repo_dir}' && {jobrc_cmd}{command}" "{log_file}"
"""


@exc.handle_exception(PermissionError, exc.JobError, message="Failed to create job script")
@exc.handle_exception(OSError, exc.JobError, message="Failed to create job script")
def _write_job_script(job_dir: pl.Path, script_content: str) -> pl.Path:
    script_path = job_dir / "run.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)
    return script_path


def _create_job_script(
    job_dir: pl.Path,
    log_file: pl.Path,
    job_repo_dir: pl.Path,
    archive_path: pl.Path,
    command: str,
    jobrc: str | None = None,
) -> pl.Path:
    script_content = _build_script_content(log_file, job_repo_dir, archive_path, command, jobrc=jobrc)
    return _write_job_script(job_dir, script_content=script_content)


@exc.handle_exception(Exception, exc.JobError, message="Failed to build job.env")
def _build_environment(gpu_idxs: list[int], job_env: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ",".join([str(i) for i in gpu_idxs])
    env.update(job_env)
    return env


@exc.handle_exception(Exception, message="Error determining exit code", reraise=False, default_return=None)
async def _get_job_exit_code(job_id: str, job_dir: pl.Path | None) -> int | None:
    if job_dir is None:
        logger.warning(f"No directory specified for job {job_id}, cannot determine exit code")
        return None

    content = await async_get_job_logs(job_dir=job_dir, last_n_lines=1)
    if content is None:
        logger.warning(f"No output log found for job {job_id}")
        raise exc.JobError(f"No output log found for job {job_id}")

    return _parse_exit_code(content.strip())


@exc.handle_exception(PermissionError, exc.JobError, message="Cannot read job log file")
@exc.handle_exception(OSError, exc.JobError, message="Cannot read job log file")
def _read_log_file(log_path: pl.Path, last_n_lines: int | None = None) -> str:
    if last_n_lines is None:
        return log_path.read_text()
    else:
        with log_path.open() as f:
            return "".join(f.readlines()[-last_n_lines:])


@exc.handle_exception(ValueError, message="Invalid exit code format", reraise=False, default_return=None)
def _parse_exit_code(last_line: str) -> int:
    match = re.search(r'COMMAND_EXIT_CODE=["\']?(\d+)["\']?', last_line)
    if not match:
        raise exc.JobError(message="Could not find exit code in log")
    return int(match.group(1))


@exc.handle_exception(FileNotFoundError, exc.JobError, message="Cannot launch job process - file not found")
@exc.handle_exception(PermissionError, exc.JobError, message="Cannot launch job process - permission denied")
async def _launch_screen_process(session_name: str, script_path: str, env: dict[str, str]) -> int:
    abs_script_path = pl.Path(script_path).absolute()

    if not abs_script_path.exists():
        raise exc.JobError(message=f"Script path does not exist: {abs_script_path}")

    if not os.access(abs_script_path, os.X_OK):
        try:
            abs_script_path.chmod(0o755)
        except Exception:
            raise exc.JobError(message=f"Script not executable: {abs_script_path}")

    process = await asyncio.create_subprocess_exec(
        "screen",
        "-dmS",
        session_name,
        str(abs_script_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await process.communicate()
    if process.returncode != 0:
        raise exc.JobError(message=f"Screen process exited with code {process.returncode}")

    await asyncio.sleep(0.5)

    proc = await asyncio.create_subprocess_exec("pgrep", "-f", session_name, stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    pids = [p for p in stdout.decode().strip().split("\n") if p]

    if pids:
        return int(pids[0])

    raise exc.JobError(message=f"Failed to get PID for job in session {session_name}")


####################


def create_job(
    command: str,
    artifact_id: str,
    user: str,
    node_name: str,
    num_gpus: int,
    env: dict[str, str],
    jobrc: str | None,
    priority: int,
    integrations: list[schemas.IntegrationType],
    notifications: list[schemas.NotificationType],
    git_repo_url: str | None = None,
    git_branch: str | None = None,
    gpu_idxs: list[int] | None = None,
    ignore_blacklist: bool = False,
) -> schemas.Job:
    return schemas.Job(
        id=_generate_job_id(),
        command=command.strip(),
        status="queued",
        created_at=dt.datetime.now().timestamp(),
        user=user,
        artifact_id=artifact_id,
        git_repo_url=git_repo_url,
        git_branch=git_branch,
        node_name=node_name,
        priority=priority,
        num_gpus=num_gpus,
        env=env,
        jobrc=jobrc,
        integrations=integrations,
        notifications=notifications,
        notification_messages={},
        pid=None,
        dir=None,
        screen_session_name=None,
        started_at=None,
        gpu_idxs=gpu_idxs or [],
        wandb_url=None,
        marked_for_kill=False,
        ignore_blacklist=ignore_blacklist,
        completed_at=None,
        exit_code=None,
        error_message=None,
    )


@exc.handle_exception(Exception, exc.JobError, message="Failed to start job")
async def async_start_job(job: schemas.Job, gpu_idxs: list[int], ctx) -> schemas.Job:
    job_dir = pl.Path(tempfile.mkdtemp(prefix=f"nexus-job-{job.id}-"))
    job_dir.mkdir(parents=True, exist_ok=True)
    job = dc.replace(job, dir=job_dir)

    if job.dir is None:
        raise exc.JobError(message=f"Job directory not set for job {job.id}")

    log_file, job_repo_dir, env, script_path = await prepare_job_environment(job, gpu_idxs, ctx)
    session_name = _get_job_session_name(job.id)

    pid = await _launch_screen_process(session_name, str(script_path), env)
    return dc.replace(
        job,
        started_at=dt.datetime.now().timestamp(),
        gpu_idxs=gpu_idxs,
        status="running",
        pid=pid,
        screen_session_name=session_name,
    )


@exc.handle_exception(
    subprocess.CalledProcessError, message="Error checking process status", reraise=False, default_return=False
)
def is_job_running(job: schemas.Job) -> bool:
    if job.pid is None:
        session_name = _get_job_session_name(job.id)
        output = subprocess.check_output(["screen", "-ls"], stderr=subprocess.DEVNULL, text=True)
        return session_name in output

    try:
        os.kill(job.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def async_cleanup_job_repo(job_dir: pl.Path | None) -> None:
    if job_dir is None:
        return None

    job_repo_dir = job_dir / "repo"
    if job_repo_dir.exists():
        shutil.rmtree(job_repo_dir, ignore_errors=True)
        logger.info(f"Successfully cleaned up {job_repo_dir}")


async def async_end_job(_job: schemas.Job, killed: bool) -> schemas.Job:
    job_log = await async_get_job_logs(job_dir=_job.dir)
    exit_code = await _get_job_exit_code(job_id=_job.id, job_dir=_job.dir)
    completed_at = dt.datetime.now().timestamp()

    if killed:
        new_job = dc.replace(_job, status="killed", completed_at=completed_at)
    elif job_log is None:
        new_job = dc.replace(
            _job, status="failed", error_message="No output log found", completed_at=dt.datetime.now().timestamp()
        )
    elif exit_code is None:
        new_job = dc.replace(
            _job,
            status="failed",
            error_message="Could not find exit code in log",
            completed_at=completed_at,
        )
    else:
        new_job = dc.replace(
            _job,
            exit_code=exit_code,
            status="completed" if exit_code == 0 else "failed",
            error_message=None if exit_code == 0 else f"Job failed with exit code {exit_code}",
            completed_at=completed_at,
        )

    return new_job


async def async_get_job_logs(job_dir: pl.Path | None, last_n_lines: int | None = None) -> str | None:
    if job_dir is None:
        return None

    logs = job_dir / "output.log"
    if not logs.exists():
        return None

    return await asyncio.to_thread(_read_log_file, logs, last_n_lines)


@exc.handle_exception(subprocess.SubprocessError, exc.JobError, message="Failed to kill job processes")
async def kill_job(job: schemas.Job) -> None:
    if job.dir is not None:
        job_dir = str(job.dir)
        logger.debug(f"Killing any processes running in directory {job_dir}")
        await asyncio.create_subprocess_shell(f"pkill -9 -f {job_dir}")

    session_name = _get_job_session_name(job.id)
    logger.debug(f"Killing any processes containing session name {session_name}")
    await asyncio.create_subprocess_shell(f"pkill -9 -f {session_name}")

    if job.pid is not None:
        logger.debug(f"Killing any child processes of {job.pid}")
        pgid_proc = await asyncio.create_subprocess_shell(
            f"ps -o pgid= -p {job.pid} 2>/dev/null", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await pgid_proc.communicate()
        pgid = stdout.decode().strip()
        if pgid:
            logger.debug(f"Killing process group {pgid}")
            await asyncio.create_subprocess_shell(f"kill -9 -{pgid}")


def get_queue(queued_jobs: list[schemas.Job]) -> list[schemas.Job]:
    if not queued_jobs:
        return []

    sorted_jobs = sorted(queued_jobs, key=lambda x: (x.priority), reverse=True)

    return sorted_jobs


async def prepare_job_environment(
    job: schemas.Job, gpu_idxs: list[int], ctx
) -> tuple[pl.Path, pl.Path, dict[str, str], pl.Path]:
    if job.dir is None or not job.artifact_id:
        raise exc.JobError(message="Job directory or artifact_id not set")

    log_file, job_repo_dir = await asyncio.to_thread(_create_directories, job.dir)
    env = await asyncio.to_thread(_build_environment, gpu_idxs, job.env)

    archive_path = job.dir / "code.tar"
    archive_path.write_bytes(db.get_artifact(ctx.db, job.artifact_id))

    script_path = await asyncio.to_thread(
        _create_job_script, job.dir, log_file, job_repo_dir, archive_path, job.command, job.jobrc
    )

    return log_file, job_repo_dir, env, script_path
