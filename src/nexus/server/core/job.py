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

SCREENRC_CONTENT = """termcapinfo xterm*|rxvt*|kterm*|Eterm*|alacritty*|kitty*|screen* ti@:te@

defscrollback 10000
"""


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


def _build_job_commands_script(
    job_repo_dir: pl.Path,
    archive_path: pl.Path,
    command: str,
    jobrc: str | None = None,
) -> str:
    jobrc_section = ""
    if jobrc and jobrc.strip():
        jobrc_section = f"""
echo "Running jobrc..."
{jobrc.strip()}
"""

    return f"""#!/bin/bash
set -euo pipefail

echo "### PRESS CTRL+A, THEN D TO DISCONNECT FROM SCREEN SESSION ###"
echo ""
echo "Extracting repository..."
mkdir -p {job_repo_dir}
tar -xf {archive_path} -C {job_repo_dir}
cd '{job_repo_dir}'
{jobrc_section}
echo "Running command..."
echo "$ {command}"
{command}
"""


def _build_script_content(
    log_file: pl.Path,
    job_commands_script: pl.Path,
) -> str:
    error_log = log_file.parent / "error.log"

    return f"""#!/bin/bash
set -euo pipefail
exec 2>"{error_log}"

echo "Starting job execution..." >&2

if ! script -q -e -f -c "bash {job_commands_script}" "{log_file}"; then
    echo "Job script failed with exit code $?" >&2
    exit 1
fi

echo "Job completed successfully" >&2
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
    job_commands_script_path = job_dir / "job_commands.sh"
    job_commands_content = _build_job_commands_script(job_repo_dir, archive_path, command, jobrc=jobrc)
    job_commands_script_path.write_text(job_commands_content)
    job_commands_script_path.chmod(0o755)

    script_content = _build_script_content(log_file, job_commands_script_path)
    return _write_job_script(job_dir, script_content=script_content)


@exc.handle_exception(Exception, exc.JobError, message="Failed to build job.env")
def _build_environment(gpu_idxs: list[int], job_env: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    if gpu_idxs:
        env["CUDA_VISIBLE_DEVICES"] = ",".join([str(i) for i in gpu_idxs])
    else:
        env["CUDA_VISIBLE_DEVICES"] = ""
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


def _create_screenrc() -> pl.Path:
    screenrc_path = pl.Path(tempfile.mktemp(suffix=".screenrc"))
    screenrc_path.write_text(SCREENRC_CONTENT)
    return screenrc_path


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

    logger.debug(f"Starting screen session {session_name} with script {abs_script_path}")
    script_content = abs_script_path.read_text()
    logger.debug(f"Outer script (run.sh):\n{script_content}")

    job_commands_script = abs_script_path.parent / "job_commands.sh"
    if job_commands_script.exists():
        job_commands_content = job_commands_script.read_text()
        logger.debug(f"Inner script (job_commands.sh):\n{job_commands_content}")

    syntax_check = await asyncio.create_subprocess_exec(
        "bash", "-n", str(abs_script_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await syntax_check.communicate()
    if syntax_check.returncode != 0:
        logger.error(f"Script has syntax errors:\n{stderr.decode()}")
        raise exc.JobError(message=f"Script syntax error: {stderr.decode()}")

    screenrc_path = _create_screenrc()
    process = await asyncio.create_subprocess_exec(
        "screen",
        "-c",
        str(screenrc_path),
        "-dmS",
        session_name,
        str(abs_script_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()
    logger.debug(f"Screen command returncode: {process.returncode}")
    if stdout:
        logger.debug(f"Screen stdout: {stdout.decode().strip()}")
    if stderr:
        logger.debug(f"Screen stderr: {stderr.decode().strip()}")

    if process.returncode != 0:
        error_details = f"Screen process exited with code {process.returncode}"
        if stdout:
            error_details += f"\nStdout: {stdout.decode().strip()}"
        if stderr:
            error_details += f"\nStderr: {stderr.decode().strip()}"

        script_content = abs_script_path.read_text()
        logger.error(f"Failed script content:\n{script_content}")

        raise exc.JobError(message=error_details)

    await asyncio.sleep(0.5)

    screen_list = await asyncio.create_subprocess_exec(
        "screen", "-ls", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    screen_stdout, _ = await screen_list.communicate()
    logger.debug(f"Active screen sessions:\n{screen_stdout.decode()}")

    proc = await asyncio.create_subprocess_exec("pgrep", "-f", session_name, stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    pids = [p for p in stdout.decode().strip().split("\n") if p]
    logger.debug(f"pgrep results for '{session_name}': {pids}")

    if pids:
        return int(pids[0])

    logger.error(f"Failed to find PID for session {session_name}")

    script_dir = abs_script_path.parent
    output_log = script_dir / "output.log"
    error_log = script_dir / "error.log"

    if error_log.exists():
        try:
            error_content = error_log.read_text()
            if error_content.strip():
                logger.error(f"Job error log:\n{error_content}")
            else:
                logger.error("Job error log is empty")
        except Exception as e:
            logger.error(f"Could not read error log: {e}")
    else:
        logger.error(f"Error log does not exist at: {error_log}")

    if output_log.exists():
        try:
            log_content = output_log.read_text()
            if log_content.strip():
                logger.error(f"Job output log:\n{log_content}")
            else:
                logger.error("Job output log is empty")
        except Exception as e:
            logger.error(f"Could not read output log: {e}")
    else:
        logger.error(f"Output log does not exist at: {output_log}")

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
    git_tag: str | None = None,
    gpu_idxs: list[int] | None = None,
    ignore_blacklist: bool = False,
    job_id: str | None = None,
) -> schemas.Job:
    return schemas.Job(
        id=job_id or _generate_job_id(),
        command=command.strip(),
        artifact_id=artifact_id,
        git_repo_url=git_repo_url,
        git_branch=git_branch,
        git_tag=git_tag,
        status="queued",
        created_at=dt.datetime.now().timestamp(),
        priority=priority,
        num_gpus=num_gpus,
        env=env,
        node_name=node_name,
        jobrc=jobrc,
        integrations=integrations,
        notifications=notifications,
        notification_messages={},
        pid=None,
        dir=None,
        started_at=None,
        gpu_idxs=gpu_idxs or [],
        wandb_url=None,
        marked_for_kill=False,
        completed_at=None,
        exit_code=None,
        error_message=None,
        user=user,
        ignore_blacklist=ignore_blacklist,
        screen_session_name=None,
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
        logger.debug(f"Successfully cleaned up {job_repo_dir}")


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


async def _get_process_group(pid: int) -> str | None:
    pgid_proc = await asyncio.create_subprocess_shell(
        f"ps -o pgid= -p {pid} 2>/dev/null", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await pgid_proc.communicate()
    pgid = stdout.decode().strip()
    return pgid if pgid else None


async def _send_signal_to_pgid(pgid: str, signal: int) -> None:
    kill_proc = await asyncio.create_subprocess_shell(
        f"kill -{signal} -{pgid} 2>/dev/null", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await kill_proc.wait()


async def _pkill_processes(job_dir: str, signal: int) -> None:
    pkill_proc = await asyncio.create_subprocess_shell(
        f"pkill -{signal} -f {job_dir} 2>/dev/null", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await pkill_proc.wait()


async def _terminate_screen_session(session_name: str) -> None:
    screen_proc = await asyncio.create_subprocess_shell(
        f"screen -S {session_name} -X quit 2>/dev/null", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await screen_proc.wait()


@exc.handle_exception(subprocess.SubprocessError, exc.JobError, message="Failed to kill job processes")
async def kill_job(job: schemas.Job) -> None:
    session_name = _get_job_session_name(job.id)

    logger.debug(f"Phase 1: Sending SIGTERM for graceful shutdown of job {job.id}")

    if job.pid is not None:
        pgid = await _get_process_group(job.pid)
        if pgid:
            logger.debug(f"Sending SIGTERM to process group {pgid}")
            await _send_signal_to_pgid(pgid, signal=15)

    if job.dir is not None:
        job_dir = str(job.dir)
        logger.debug(f"Sending SIGTERM to processes in {job_dir}")
        await _pkill_processes(job_dir, signal=15)

    logger.debug("Waiting 10 seconds for graceful shutdown")
    await asyncio.sleep(10)

    if not is_job_running(job):
        logger.debug(f"Job {job.id} terminated gracefully")
        return

    logger.debug(f"Phase 2: Job {job.id} still running, escalating to SIGKILL")

    await _terminate_screen_session(session_name)

    if job.pid is not None:
        pgid = await _get_process_group(job.pid)
        if pgid:
            logger.debug(f"Sending SIGKILL to process group {pgid}")
            await _send_signal_to_pgid(pgid, signal=9)

    if job.dir is not None:
        job_dir = str(job.dir)
        logger.debug(f"Sending SIGKILL to remaining processes in {job_dir}")
        await _pkill_processes(job_dir, signal=9)


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
