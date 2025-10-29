import dataclasses as dc
import functools
import json
import pathlib as pl
import re
import sqlite3
import time
import typing as tp

from nexus.server.core import context, schemas
from nexus.server.core import exceptions as exc
from nexus.server.utils import logger

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
    "get_artifact_id_by_git_sha",
    "is_artifact_in_use",
    "delete_artifact",
    "safe_transaction",
]


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database tables")
def _create_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT,
            artifact_id TEXT NOT NULL,
            git_repo_url TEXT,
            git_branch TEXT,
            git_tag TEXT,
            status TEXT,
            created_at REAL,
            priority INTEGER,
            num_gpus INTEGER,
            env JSON, 
            node_name TEXT,
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
            gpu_idx INTEGER PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            created_at REAL NOT NULL,
            data BLOB NOT NULL,
            git_sha TEXT
        )
    """)

    cur.execute("PRAGMA table_info(jobs)")
    columns = [col[1] for col in cur.fetchall()]
    if "git_tag" not in columns:
        cur.execute("ALTER TABLE jobs ADD COLUMN git_tag TEXT")

    cur.execute("PRAGMA table_info(artifacts)")
    artifact_columns = [col[1] for col in cur.fetchall()]
    if "git_sha" not in artifact_columns:
        cur.execute("ALTER TABLE artifacts ADD COLUMN git_sha TEXT")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_git_sha ON artifacts(git_sha)")

    conn.commit()


@exc.handle_exception(
    json.JSONDecodeError,
    exc.DatabaseError,
    message="Invalid environment data in database",
    reraise=False,
    default_return={},
)
def _parse_json(json_obj: str | None) -> dict[str, str]:
    if not json_obj:
        return {}
    if isinstance(json_obj, float | int | bool):
        return {}
    if isinstance(json_obj, str) and not json_obj.strip():
        return {}
    return json.loads(json_obj)


_DB_COLS = [
    "id",
    "command",
    "artifact_id",
    "git_repo_url",
    "git_branch",
    "git_tag",
    "status",
    "created_at",
    "priority",
    "num_gpus",
    "env",
    "node_name",
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
        job.git_tag,
        job.status,
        job.created_at,
        job.priority,
        job.num_gpus,
        json.dumps({}) if job.status in ["failed", "completed"] else json.dumps(job.env),
        job.node_name,
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


def _row_to_job(row: sqlite3.Row) -> schemas.Job:
    return schemas.Job(
        id=row["id"],
        command=row["command"],
        user=row["user"],
        artifact_id=row["artifact_id"],
        git_repo_url=row["git_repo_url"],
        git_branch=row["git_branch"],
        git_tag=row["git_tag"],
        priority=row["priority"],
        num_gpus=row["num_gpus"],
        node_name=row["node_name"],
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


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job")
def _query_job(conn: sqlite3.Connection, job_id: str) -> schemas.Job:
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


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list jobs")
def _query_jobs(conn: sqlite3.Connection, status: str | None, command_regex: str | None = None) -> list[schemas.Job]:
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

    jobs = []
    for row in rows:
        try:
            jobs.append(_row_to_job(row=row))
        except Exception as e:
            job_id = row["id"] if "id" in row.keys() else "unknown"
            logger.error(f"Failed to load job {job_id} from database: {e}. Skipping corrupted record.")

    return jobs


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job status")
def _check_job_status(conn: sqlite3.Connection, job_id: str) -> str:
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
def _delete_job(conn: sqlite3.Connection, job_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def _validate_gpu_idx(gpu_idx: int) -> None:
    if gpu_idx < 0:
        raise exc.GPUError(message=f"Invalid GPU index: {gpu_idx}. Must be a non-negative integer.")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to blacklist GPU")
def _add_gpu_to_blacklist(conn: sqlite3.Connection, gpu_idx: int) -> bool:
    """Add GPU to blacklist. Returns True if added, False if already blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    if cur.fetchone():
        return False
    cur.execute("INSERT INTO blacklisted_gpus (gpu_idx) VALUES (?)", (gpu_idx,))
    return True


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to remove GPU from blacklist")
def _remove_gpu_from_blacklist(conn: sqlite3.Connection, gpu_idx: int) -> bool:
    """Remove GPU from blacklist. Returns True if removed, False if not blacklisted."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM blacklisted_gpus WHERE gpu_idx = ?", (gpu_idx,))
    return True


####################


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database connection")
def create_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_tables(conn=conn)
    return conn


@exc.handle_exception(sqlite3.IntegrityError, exc.JobError, message="Job already exists")
@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to add job to database")
def add_job(conn: sqlite3.Connection, job: schemas.Job) -> None:
    if job.status != "queued":
        job = dc.replace(job, status="queued")

    cur = conn.cursor()
    cur.execute(_INSERT_SQL, _job_to_row(job))


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to update job")
def update_job(conn: sqlite3.Connection, job: schemas.Job) -> None:
    cur = conn.cursor()
    row_data = _job_to_row(job)
    # For UPDATE we need id at the end for WHERE clause
    update_params = row_data[1:] + (row_data[0],)
    cur.execute(_UPDATE_SQL, update_params)

    if cur.rowcount == 0:
        raise exc.JobNotFoundError(message="Job not found")


def get_job(conn: sqlite3.Connection, job_id: str) -> schemas.Job:
    _validate_job_id(job_id)
    return _query_job(conn=conn, job_id=job_id)


def list_jobs(
    conn: sqlite3.Connection,
    status: str | None = None,
    command_regex: str | None = None,
) -> list[schemas.Job]:
    _validate_job_status(status)
    return _query_jobs(conn=conn, status=status, command_regex=command_regex)


def delete_queued_job(conn: sqlite3.Connection, job_id: str) -> None:
    _validate_job_id(job_id)
    job = _query_job(conn=conn, job_id=job_id)
    status = job.status
    _verify_job_is_queued(job_id, status)
    _delete_job(conn=conn, job_id=job_id)
    if job.artifact_id and not is_artifact_in_use(conn=conn, artifact_id=job.artifact_id):
        delete_artifact(conn=conn, artifact_id=job.artifact_id)
        logger.info(f"Deleted artifact {job.artifact_id} as it's no longer needed after job {job_id} was removed")


def add_blacklisted_gpu(conn: sqlite3.Connection, gpu_idx: int) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _add_gpu_to_blacklist(conn=conn, gpu_idx=gpu_idx)


def remove_blacklisted_gpu(conn: sqlite3.Connection, gpu_idx: int) -> bool:
    _validate_gpu_idx(gpu_idx)
    return _remove_gpu_from_blacklist(conn=conn, gpu_idx=gpu_idx)


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list blacklisted GPUs")
def list_blacklisted_gpus(conn: sqlite3.Connection) -> list[int]:
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
            logger.error(f"Transaction failed, rolling back: {str(e)}")
            ctx.db.rollback()
            raise

    return wrapper


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to add artifact")
def add_artifact(conn: sqlite3.Connection, artifact_id: str, data: bytes, git_sha: str | None = None) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO artifacts (id, size, created_at, data, git_sha) VALUES (?, ?, ?, ?, ?)",
        (artifact_id, len(data), time.time(), data, git_sha),
    )
    conn.commit()


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to retrieve artifact")
def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> bytes:
    cur = conn.cursor()
    cur.execute("SELECT data FROM artifacts WHERE id = ?", (artifact_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobError(message=f"Artifact not found: {artifact_id}")
    return row["data"]


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query artifact by git SHA")
def get_artifact_id_by_git_sha(conn: sqlite3.Connection, git_sha: str) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM artifacts WHERE git_sha = ?", (git_sha,))
    row = cur.fetchone()
    return row["id"] if row else None


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to check for artifact usage")
def is_artifact_in_use(conn: sqlite3.Connection, artifact_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM jobs WHERE artifact_id = ? AND status = 'queued'", (artifact_id,))
    row = cur.fetchone()
    return row["count"] > 0


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to delete artifact")
def delete_artifact(conn: sqlite3.Connection, artifact_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    conn.commit()
