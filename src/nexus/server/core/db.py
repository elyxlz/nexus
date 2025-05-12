import dataclasses as dc
import functools
import json
import pathlib as pl
import re
import time
import typing as tp

from pyrqlite.connections import Connection as RqliteConnection

from nexus.server.core import context, schemas
from nexus.server.core import exceptions as exc
from nexus.server.utils import logger

# Define Row type for database rows
Row = dict[str, tp.Any]

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
    "add_artifact",
    "get_artifact",
    "is_artifact_in_use",
    "delete_artifact",
    "safe_transaction",
    "claim_job",
]


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to create database tables")
def _create_tables(conn: RqliteConnection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT,
            artifact_id TEXT NOT NULL,
            git_repo_url TEXT,
            git_branch TEXT,
            status TEXT,
            created_at REAL,
            priority INTEGER,
            num_gpus INTEGER,
            env JSON, 
            node TEXT,
            jobrc TEXT,
            integrations TEXT,
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
            node TEXT,
            gpu_idx INTEGER,
            PRIMARY KEY(node, gpu_idx)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            created_at REAL NOT NULL,
            data BLOB NOT NULL
        )
    """)
    conn.commit()


@exc.handle_exception(json.JSONDecodeError, exc.DatabaseError, message="Invalid environment data in database")
def _parse_json(json_obj: str | None) -> dict[str, str]:
    if not json_obj:
        return {}
    if isinstance(json_obj, float | int | bool):
        return {}
    return json.loads(json_obj)


_DB_COLS = [
    "id",
    "command",
    "artifact_id",
    "git_repo_url",
    "git_branch",
    "status",
    "created_at",
    "priority",
    "num_gpus",
    "env",
    "node",
    "jobrc",
    "integrations",
    "notifications",
    "notification_messages",
    "pid",
    "dir",
    "started_at",
    "gpu_idxs",
    "wandb_url",
    "marked_for_kill",
    "completed_at",
    "exit_code",
    "error_message",
    "user",
    "ignore_blacklist",
    "screen_session_name",
]

_INSERT_SQL = f"INSERT INTO jobs VALUES ({','.join(['?'] * len(_DB_COLS))})"
_UPDATE_SQL = f"UPDATE jobs SET {', '.join(f'{col} = ?' for col in _DB_COLS[1:])} WHERE id = ?"


def _job_to_row(job: schemas.Job) -> tuple:
    return (
        job.id,
        job.command,
        job.artifact_id,
        job.git_repo_url,
        job.git_branch,
        job.status,
        job.created_at,
        job.priority,
        job.num_gpus,
        json.dumps({}) if job.status in ["failed", "completed"] else json.dumps(job.env),
        job.node,
        job.jobrc,
        ",".join(job.integrations),
        ",".join(job.notifications),
        json.dumps(job.notification_messages),
        job.pid,
        str(job.dir) if job.dir else None,
        job.started_at,
        ",".join(map(str, job.gpu_idxs)),
        job.wandb_url,
        int(job.marked_for_kill),
        job.completed_at,
        job.exit_code,
        job.error_message,
        job.user,
        int(job.ignore_blacklist),
        job.screen_session_name,
    )


def _row_to_job(row: Row) -> schemas.Job:
    return schemas.Job(
        id=row["id"],
        command=row["command"],
        user=row["user"],
        artifact_id=row["artifact_id"],
        git_repo_url=row["git_repo_url"],
        git_branch=row["git_branch"],
        priority=row["priority"],
        num_gpus=row["num_gpus"],
        node=row["node"],
        env=_parse_json(json_obj=row["env"]),
        jobrc=row["jobrc"],
        notifications=row["notifications"].split(",") if row["notifications"] else [],
        integrations=row["integrations"].split(",") if row["integrations"] else [],
        status=row["status"],
        created_at=row["created_at"],
        notification_messages=_parse_json(json_obj=row["notification_messages"]),
        pid=row["pid"],
        dir=pl.Path(row["dir"]) if row["dir"] else None,
        started_at=row["started_at"],
        gpu_idxs=[int(i) for i in row["gpu_idxs"].split(",")] if row["gpu_idxs"] else [],
        wandb_url=row["wandb_url"],
        marked_for_kill=bool(row["marked_for_kill"]) if row["marked_for_kill"] is not None else False,
        ignore_blacklist=bool(row["ignore_blacklist"]),
        screen_session_name=row["screen_session_name"] if "screen_session_name" in row.keys() else None,
        completed_at=row["completed_at"],
        exit_code=row["exit_code"],
        error_message=row["error_message"],
    )


def _validate_job_id(job_id: str) -> None:
    if not job_id:
        raise exc.JobError(message="Job ID cannot be empty")


@exc.handle_exception(exc.JobNotFoundError, message="Job not found error", reraise=True)
@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to query job")
def _query_job(conn: RqliteConnection, job_id: str) -> schemas.Job:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobNotFoundError(message=f"Job not found: {job_id}")
    return _row_to_job(row=row)


def _validate_job_status(status: str | None) -> None:
    if status is not None:
        valid_statuses = {"queued", "running", "completed", "failed", "killed"}
        if status not in valid_statuses:
            raise exc.JobError(message=f"Invalid job status: {status}. Must be one of {', '.join(valid_statuses)}")


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to list jobs")
def _query_jobs(conn: RqliteConnection, status: str | None, command_regex: str | None = None) -> list[schemas.Job]:
    cur = conn.cursor()

    query = "SELECT * FROM jobs"
    params = []
    conditions = []

    if status is not None:
        conditions.append("status = ?")
        params.append(status)

    # For command_regex, we need to use a different approach with pyrqlite
    # as it doesn't support the create_function method
    if command_regex is not None:
        # Don't include the regex in the SQL query - we'll filter after
        pass

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    # Execute query
    cur.execute(query, params)
    rows = cur.fetchall()

    # Apply regex filter if needed
    if command_regex is not None:
        # Manual post-query filtering
        filtered_rows = []
        pattern = re.compile(command_regex)
        for row in rows:
            command = row.get("command", "")
            if command and pattern.search(command):
                filtered_rows.append(row)
        rows = filtered_rows

    return [_row_to_job(row=row) for row in rows]


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to query job status")
def _check_job_status(conn: RqliteConnection, job_id: str) -> str:
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


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to delete job")
def _delete_job(conn: RqliteConnection, job_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def _validate_gpu_idx(gpu_idx: int) -> None:
    if gpu_idx < 0:
        raise exc.GPUError(message=f"Invalid GPU index: {gpu_idx}. Must be a non-negative integer.")


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to blacklist GPU")
def _add_gpu_to_blacklist(conn: RqliteConnection, node: str, gpu_idx: int) -> bool:
    """Add GPU to blacklist. Returns True if added, False if already blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE node = ? AND gpu_idx = ?", (node, gpu_idx))
    if cur.fetchone():
        return False
    cur.execute("INSERT INTO blacklisted_gpus (node, gpu_idx) VALUES (?, ?)", (node, gpu_idx))
    return True


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to remove GPU from blacklist")
def _remove_gpu_from_blacklist(conn: RqliteConnection, node: str, gpu_idx: int) -> bool:
    """Remove GPU from blacklist. Returns True if removed, False if not blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE node = ? AND gpu_idx = ?", (node, gpu_idx))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM blacklisted_gpus WHERE node = ? AND gpu_idx = ?", (node, gpu_idx))
    return True


####################
@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to create database connection")
def create_connection(host: str, port: int, api_key: str) -> RqliteConnection:
    from nexus.server.core import rqlite

    conn = rqlite.connect_with_params(host, port, api_key)
    _create_tables(conn=conn)
    return conn


@exc.handle_exception(Exception, exc.JobError, message="Job already exists")
@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to add job to database")
def add_job(conn: RqliteConnection, job: schemas.Job) -> None:
    if job.status != "queued":
        job = dc.replace(job, status="queued")

    cur = conn.cursor()
    cur.execute(_INSERT_SQL, _job_to_row(job))


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to update job")
def update_job(conn: RqliteConnection, job: schemas.Job) -> None:
    cur = conn.cursor()
    row_data = _job_to_row(job)
    # For UPDATE we need id at the end for WHERE clause
    update_params = row_data[1:] + (row_data[0],)
    cur.execute(_UPDATE_SQL, update_params)

    if cur.rowcount == 0:
        raise exc.JobNotFoundError(message="Job not found")


def get_job(conn: RqliteConnection, job_id: str) -> schemas.Job:
    _validate_job_id(job_id)
    return _query_job(conn=conn, job_id=job_id)


def list_jobs(
    conn: RqliteConnection,
    status: str | None = None,
    command_regex: str | None = None,
) -> list[schemas.Job]:
    _validate_job_status(status)
    return _query_jobs(conn=conn, status=status, command_regex=command_regex)


@exc.handle_exception(exc.JobNotFoundError, message="Job not found", reraise=True)
def delete_queued_job(conn: RqliteConnection, job_id: str) -> None:
    _validate_job_id(job_id)
    job = _query_job(conn=conn, job_id=job_id)
    status = job.status
    _verify_job_is_queued(job_id, status)
    _delete_job(conn=conn, job_id=job_id)
    if job.artifact_id and not is_artifact_in_use(conn=conn, artifact_id=job.artifact_id):
        delete_artifact(conn=conn, artifact_id=job.artifact_id)
        logger.info(f"Deleted artifact {job.artifact_id} as it's no longer needed after job {job_id} was removed")


def add_blacklisted_gpu(conn: RqliteConnection, gpu_idx: int, node: str) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _add_gpu_to_blacklist(conn=conn, node=node, gpu_idx=gpu_idx)


def remove_blacklisted_gpu(conn: RqliteConnection, gpu_idx: int, node: str) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _remove_gpu_from_blacklist(conn=conn, node=node, gpu_idx=gpu_idx)


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to list blacklisted GPUs")
def list_blacklisted_gpus(conn: RqliteConnection, node: str) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT gpu_idx FROM blacklisted_gpus WHERE node = ?", (node,))
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
            logger.error(f"Transaction failed, rolling back: {str(e)}")
            ctx.db.rollback()
            raise

    return wrapper


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to add artifact")
def add_artifact(conn: RqliteConnection, artifact_id: str, data: bytes) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO artifacts (id, size, created_at, data) VALUES (?, ?, ?, ?)",
        (artifact_id, len(data), time.time(), data),
    )
    conn.commit()


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to retrieve artifact")
def get_artifact(conn: RqliteConnection, artifact_id: str) -> bytes:
    cur = conn.cursor()
    cur.execute("SELECT data FROM artifacts WHERE id = ?", (artifact_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobError(message=f"Artifact not found: {artifact_id}")
    return row["data"]


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to check for artifact usage")
def is_artifact_in_use(conn: RqliteConnection, artifact_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM jobs WHERE artifact_id = ? AND status = 'queued'", (artifact_id,))
    row = cur.fetchone()
    if row is None:
        return False
    return row["count"] > 0


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to delete artifact")
def delete_artifact(conn: RqliteConnection, artifact_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    conn.commit()


@exc.handle_exception(Exception, exc.DatabaseError, message="Failed to claim job")
def claim_job(conn: RqliteConnection, job_id: str, node: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "UPDATE jobs SET node = ? WHERE id = ? AND node IS NULL AND status = 'queued'",
        (node, job_id),
    )
    return cur.rowcount == 1
