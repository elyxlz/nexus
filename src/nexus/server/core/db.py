import functools
import json
import pathlib as pl
import re
import sqlite3
import typing as tp

from nexus.server.core import context, logger, schemas
from nexus.server.core import exceptions as exc

__all__ = [
    "create_connection",
    "add_job",
    "update_job",
    "get_job",
    "list_jobs",
    "delete_queued_job",
    "add_blacklisted_gpu",
    "remove_blacklisted_gpu",
    "list_blacklisted_gpus",
    "safe_transaction",
]


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database tables")
def _create_tables(_logger: logger.NexusServerLogger, conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT,
            git_repo_url TEXT,
            git_tag TEXT,
            git_branch TEXT,
            status TEXT,
            created_at REAL,
            priority INTEGER,
            num_gpus INTEGER,
            env JSON, 
            node_name TEXT,
            jobrc TEXT,
            search_wandb INTEGER,
            notifications TEXT,
            notification_messages JSON,
            pid INTEGER,
            dir TEXT,
            started_at REAL,
            gpu_idxs TEXT,
            wandb_url TEXT,
            marked_for_kill INTEGER,
            completed_at REAL,
            exit_code INTEGER,
            error_message TEXT,
            user TEXT,
            ignore_blacklist INTEGER DEFAULT 0,
            screen_session_name TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blacklisted_gpus (
            gpu_idx INTEGER PRIMARY KEY
        )
    """)
    conn.commit()


@exc.handle_exception(json.JSONDecodeError, exc.DatabaseError, message="Invalid environment data in database")
def _parse_json(_logger: logger.NexusServerLogger, json_obj: str | None) -> dict[str, str]:
    if not json_obj:
        return {}
    return json.loads(json_obj)


def _row_to_job(_logger: logger.NexusServerLogger, row: sqlite3.Row) -> schemas.Job:
    return schemas.Job(
        id=row["id"],
        command=row["command"],
        git_repo_url=row["git_repo_url"],
        git_tag=row["git_tag"],
        git_branch=row["git_branch"],
        status=row["status"],
        created_at=row["created_at"],
        priority=row["priority"],
        num_gpus=row["num_gpus"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        node_name=row["node_name"],
        gpu_idxs=[int(i) for i in row["gpu_idxs"].split(",")] if row["gpu_idxs"] else [],
        exit_code=row["exit_code"],
        error_message=row["error_message"],
        wandb_url=row["wandb_url"],
        user=row["user"],
        marked_for_kill=bool(row["marked_for_kill"]) if row["marked_for_kill"] is not None else False,
        ignore_blacklist=bool(row["ignore_blacklist"]),
        dir=pl.Path(row["dir"]) if row["dir"] else None,
        pid=row["pid"],
        env=_parse_json(_logger, json_obj=row["env"]),
        jobrc=row["jobrc"],
        search_wandb=bool(row["search_wandb"]) if row["search_wandb"] is not None else False,
        notifications=row["notifications"].split(",") if row["notifications"] else [],
        notification_messages=_parse_json(_logger, json_obj=row["notification_messages"]),
        screen_session_name=row["screen_session_name"] if "screen_session_name" in row.keys() else None,
    )


def _validate_job_id(job_id: str) -> None:
    if not job_id:
        raise exc.JobError(message="Job ID cannot be empty")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job")
def _query_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job_id: str) -> schemas.Job:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobNotFoundError(message=f"Job not found: {job_id}")
    return _row_to_job(_logger, row=row)


def _validate_job_status(status: str | None) -> None:
    if status is not None:
        valid_statuses = {"queued", "running", "completed", "failed", "killed"}
        if status not in valid_statuses:
            raise exc.JobError(message=f"Invalid job status: {status}. Must be one of {', '.join(valid_statuses)}")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list jobs")
def _query_jobs(
    _logger: logger.NexusServerLogger, conn: sqlite3.Connection, status: str | None, command_regex: str | None = None
) -> list[schemas.Job]:
    cur = conn.cursor()

    query = "SELECT * FROM jobs"
    params = []
    conditions = []

    if status is not None:
        conditions.append("status = ?")
        params.append(status)

    if command_regex is not None:
        conditions.append("command REGEXP ?")
        params.append(command_regex)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    conn.create_function("REGEXP", 2, lambda pattern, text: bool(re.search(pattern, text or "")) if text else False)

    cur.execute(query, params)
    rows = cur.fetchall()
    return [_row_to_job(_logger, row=row) for row in rows]


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job status")
def _check_job_status(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job_id: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobNotFoundError(message=f"Job not found: {job_id}")
    return row["status"]


def _verify_job_is_queued(job_id: str, status: str) -> None:
    if status != "queued":
        raise exc.InvalidJobStateError(
            message=f"Cannot delete job {job_id} with status '{status}'. Only queued jobs can be deleted.",
        )


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to delete job")
def _delete_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def _validate_gpu_idx(gpu_idx: int) -> None:
    if gpu_idx < 0:
        raise exc.GPUError(message=f"Invalid GPU index: {gpu_idx}. Must be a non-negative integer.")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to blacklist GPU")
def _add_gpu_to_blacklist(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, gpu_idx: int) -> bool:
    """Add GPU to blacklist. Returns True if added, False if already blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    if cur.fetchone():
        return False
    cur.execute("INSERT INTO blacklisted_gpus (gpu_idx) VALUES (?)", (gpu_idx,))
    return True


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to remove GPU from blacklist")
def _remove_gpu_from_blacklist(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, gpu_idx: int) -> bool:
    """Remove GPU from blacklist. Returns True if removed, False if not blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    return True


####################


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database connection")
def create_connection(_logger: logger.NexusServerLogger, db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_tables(_logger, conn=conn)
    return conn


@exc.handle_exception(sqlite3.IntegrityError, exc.JobError, message="Job already exists")
@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to add job to database")
def add_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job: schemas.Job) -> None:
    cur = conn.cursor()
    env_json = json.dumps(job.env)
    notification_messages_json = json.dumps(job.notification_messages)
    notifications_str = ",".join(job.notifications)
    gpu_idxs_str = ",".join([str(i) for i in job.gpu_idxs])

    cur.execute(
        """
        INSERT INTO jobs (
            id, command, git_repo_url, git_tag, git_branch, status, created_at, priority,
            num_gpus, started_at, completed_at, gpu_idxs, exit_code, error_message, 
            wandb_url, user, marked_for_kill, dir, node_name,
            pid, jobrc, env, search_wandb, notifications, notification_messages, ignore_blacklist,
            screen_session_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            job.id,
            job.command,
            job.git_repo_url,
            job.git_tag,
            job.git_branch,
            job.status,
            job.created_at,
            job.priority,
            job.num_gpus,
            job.started_at,
            job.completed_at,
            gpu_idxs_str,
            job.exit_code,
            job.error_message,
            job.wandb_url,
            job.user,
            int(job.marked_for_kill),
            str(job.dir) if job.dir else None,
            job.node_name,
            job.pid,
            job.jobrc,
            env_json,
            int(job.search_wandb),
            notifications_str,
            notification_messages_json,
            int(job.ignore_blacklist),
            job.screen_session_name,
        ),
    )


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to update job")
def update_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job: schemas.Job) -> None:
    cur = conn.cursor()

    env_json = json.dumps({}) if job.status in ["failed", "completed"] else json.dumps(job.env)
    notification_messages_json = json.dumps(job.notification_messages)
    notifications_str = ",".join(job.notifications)
    gpu_idxs_str = ",".join([str(i) for i in job.gpu_idxs])

    cur.execute(
        """
        UPDATE jobs SET 
            command = ?,
            git_repo_url = ?,
            git_tag = ?,
            git_branch = ?,
            status = ?,
            created_at = ?,
            priority = ?,
            num_gpus = ?,
            started_at = ?,
            completed_at = ?,
            gpu_idxs = ?,
            exit_code = ?,
            error_message = ?,
            wandb_url = ?,
            user = ?,
            marked_for_kill = ?,
            dir = ?,
            pid = ?,
            jobrc = ?,
            env = ?,
            search_wandb = ?,
            notifications = ?,
            notification_messages = ?,
            ignore_blacklist = ?,
            screen_session_name = ?
        WHERE id = ?
    """,
        (
            job.command,
            job.git_repo_url,
            job.git_tag,
            job.git_branch,
            job.status,
            job.created_at,
            job.priority,
            job.num_gpus,
            job.started_at,
            job.completed_at,
            gpu_idxs_str,
            job.exit_code,
            job.error_message,
            job.wandb_url,
            job.user,
            int(job.marked_for_kill),
            str(job.dir) if job.dir else None,
            job.pid,
            job.jobrc,
            env_json,
            int(job.search_wandb),
            notifications_str,
            notification_messages_json,
            int(job.ignore_blacklist),
            job.screen_session_name,
            job.id,
        ),
    )

    if cur.rowcount == 0:
        raise exc.JobNotFoundError(message="Job not found")


def get_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job_id: str) -> schemas.Job:
    _validate_job_id(job_id)
    return _query_job(_logger, conn=conn, job_id=job_id)


def list_jobs(
    _logger: logger.NexusServerLogger,
    conn: sqlite3.Connection,
    status: str | None = None,
    command_regex: str | None = None,
) -> list[schemas.Job]:
    _validate_job_status(status)
    return _query_jobs(_logger, conn=conn, status=status, command_regex=command_regex)


def delete_queued_job(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, job_id: str) -> None:
    _validate_job_id(job_id)
    status = _check_job_status(_logger, conn=conn, job_id=job_id)
    _verify_job_is_queued(job_id, status)
    return _delete_job(_logger, conn=conn, job_id=job_id)


def add_blacklisted_gpu(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, gpu_idx: int) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _add_gpu_to_blacklist(_logger, conn=conn, gpu_idx=gpu_idx)


def remove_blacklisted_gpu(_logger: logger.NexusServerLogger, conn: sqlite3.Connection, gpu_idx: int) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _remove_gpu_from_blacklist(_logger, conn=conn, gpu_idx=gpu_idx)


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list blacklisted GPUs")
def list_blacklisted_gpus(_logger: logger.NexusServerLogger, conn: sqlite3.Connection) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT gpu_idx FROM blacklisted_gpus")
    rows = cur.fetchall()
    return [row["gpu_idx"] for row in rows]


def safe_transaction(func: tp.Callable[..., tp.Any]) -> tp.Callable[..., tp.Any]:
    @functools.wraps(func)
    async def wrapper(*args: tp.Any, **kwargs: tp.Any) -> tp.Any:
        ctx = None
        for arg in args:
            if isinstance(arg, context.NexusServerContext):
                ctx = arg
                break

        if ctx is None:
            for arg_value in kwargs.values():
                if isinstance(arg_value, context.NexusServerContext):
                    ctx = arg_value
                    break

        if ctx is None:
            raise exc.ServerError(message="Transaction decorator requires a NexusServerContext parameter")

        try:
            result = await func(*args, **kwargs)
            ctx.db.commit()
            return result
        except Exception as e:
            ctx.logger.error(f"Transaction failed, rolling back: {str(e)}")
            ctx.db.rollback()
            raise

    return wrapper
