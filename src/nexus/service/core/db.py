import functools
import pathlib as pl
import sqlite3
import typing

from nexus.service.core import context, logger, models
from nexus.service.core import exceptions as exc

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


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database connection")
def create_connection(_logger: logger.NexusServiceLogger, db_path: str) -> sqlite3.Connection:
    """Create a connection to the SQLite database."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    create_tables(_logger=_logger, conn=conn)
    return conn


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to create database tables")
def create_tables(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection) -> None:
    """Create necessary database tables if they don't exist."""
    cur = conn.cursor()
    # Create the jobs table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT,
            git_repo_url TEXT,
            git_tag TEXT,
            status TEXT,
            created_at REAL,
            started_at REAL,
            completed_at REAL,
            gpu_index INTEGER,
            exit_code INTEGER,
            error_message TEXT,
            wandb_url TEXT,
            user TEXT,
            discord_id TEXT,
            marked_for_kill INTEGER,
            dir TEXT
        )
    """)
    # Create the blacklisted_gpus table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blacklisted_gpus (
            gpu_index INTEGER PRIMARY KEY
        )
    """)
    conn.commit()


def row_to_job(row: sqlite3.Row) -> models.Job:
    return models.Job(
        id=row["id"],
        command=row["command"],
        git_repo_url=row["git_repo_url"],
        git_tag=row["git_tag"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        gpu_index=row["gpu_index"],
        exit_code=row["exit_code"],
        error_message=row["error_message"],
        wandb_url=row["wandb_url"],
        user=row["user"],
        discord_id=row["discord_id"],
        marked_for_kill=bool(row["marked_for_kill"]) if row["marked_for_kill"] is not None else False,
        dir=pl.Path(row["dir"]) if row["dir"] else None,
    )


@exc.handle_exception(sqlite3.IntegrityError, exc.JobError, message="Job already exists")
@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to add job to database")
def add_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job: models.Job) -> None:
    """Add a new job to the database."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            id, command, git_repo_url, git_tag, status, created_at, 
            started_at, completed_at, gpu_index, exit_code, error_message, 
            wandb_url, user, discord_id, marked_for_kill, dir
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            job.id,
            job.command,
            job.git_repo_url,
            job.git_tag,
            job.status,
            job.created_at,
            job.started_at,
            job.completed_at,
            job.gpu_index,
            job.exit_code,
            job.error_message,
            job.wandb_url,
            job.user,
            job.discord_id,
            int(job.marked_for_kill),
            str(job.dir) if job.dir else None,
        ),
    )


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to update job")
def update_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job: models.Job) -> None:
    """Update an existing job in the database."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE jobs SET 
            command = ?,
            git_repo_url = ?,
            git_tag = ?,
            status = ?,
            created_at = ?,
            started_at = ?,
            completed_at = ?,
            gpu_index = ?,
            exit_code = ?,
            error_message = ?,
            wandb_url = ?,
            user = ?,
            discord_id = ?,
            marked_for_kill = ?,
            dir = ?
        WHERE id = ?
    """,
        (
            job.command,
            job.git_repo_url,
            job.git_tag,
            job.status,
            job.created_at,
            job.started_at,
            job.completed_at,
            job.gpu_index,
            job.exit_code,
            job.error_message,
            job.wandb_url,
            job.user,
            job.discord_id,
            int(job.marked_for_kill),
            str(job.dir) if job.dir else None,
            job.id,
        ),
    )

    if cur.rowcount == 0:
        raise exc.JobError(message="Job not found")


def _validate_job_id(job_id: str) -> None:
    """Validate that a job ID is not empty."""
    if not job_id:
        raise exc.JobError(message="Job ID cannot be empty")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job")
def _query_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job_id: str) -> models.Job | None:
    """Query a job from the database by ID."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if row:
        return row_to_job(row)
    return None


def get_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job_id: str) -> models.Job | None:
    _validate_job_id(job_id)
    return _query_job(_logger=_logger, conn=conn, job_id=job_id)


def _validate_job_status(status: str | None) -> None:
    if status is not None:
        valid_statuses = {"queued", "running", "completed", "failed"}
        if status not in valid_statuses:
            raise exc.JobError(message=f"Invalid job status: {status}. Must be one of {', '.join(valid_statuses)}")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list jobs")
def _query_jobs(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, status: str | None) -> list[models.Job]:
    cur = conn.cursor()
    if status is not None:
        cur.execute("SELECT * FROM jobs WHERE status = ?", (status,))
    else:
        cur.execute("SELECT * FROM jobs")
    rows = cur.fetchall()
    return [row_to_job(row) for row in rows]


def list_jobs(
    _logger: logger.NexusServiceLogger, conn: sqlite3.Connection, status: str | None = None
) -> list[models.Job]:
    _validate_job_status(status)
    return _query_jobs(_logger=_logger, conn=conn, status=status)


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to query job status")
def _check_job_status(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job_id: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        raise exc.JobError(message=f"Job not found: {job_id}")
    return row["status"]


def _verify_job_is_queued(job_id: str, status: str) -> None:
    if status != "queued":
        raise exc.JobError(
            message=f"Cannot delete job {job_id} with status '{status}'. Only queued jobs can be deleted.",
        )


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to delete job")
def _delete_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return True


def delete_queued_job(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, job_id: str) -> bool:
    _validate_job_id(job_id)
    status = _check_job_status(_logger=_logger, conn=conn, job_id=job_id)
    _verify_job_is_queued(job_id, status)
    return _delete_job(_logger=_logger, conn=conn, job_id=job_id)


def _validate_gpu_index(gpu_index: int) -> None:
    if gpu_index < 0:
        raise exc.GPUError(message=f"Invalid GPU index: {gpu_index}. Must be a non-negative integer.")


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to blacklist GPU")
def _add_gpu_to_blacklist(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, gpu_index: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    if cur.fetchone():
        return False
    cur.execute("INSERT INTO blacklisted_gpus (gpu_index) VALUES (?)", (gpu_index,))
    return True


def add_blacklisted_gpu(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, gpu_index: int) -> bool:
    _validate_gpu_index(gpu_index)
    return _add_gpu_to_blacklist(_logger=_logger, conn=conn, gpu_index=gpu_index)


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to remove GPU from blacklist")
def _remove_gpu_from_blacklist(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, gpu_index: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    return True


def remove_blacklisted_gpu(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection, gpu_index: int) -> bool:
    _validate_gpu_index(gpu_index)
    return _remove_gpu_from_blacklist(_logger=_logger, conn=conn, gpu_index=gpu_index)


@exc.handle_exception(sqlite3.Error, exc.DatabaseError, message="Failed to list blacklisted GPUs")
def list_blacklisted_gpus(_logger: logger.NexusServiceLogger, conn: sqlite3.Connection) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT gpu_index FROM blacklisted_gpus")
    rows = cur.fetchall()
    return [row["gpu_index"] for row in rows]


def safe_transaction(func: typing.Callable[..., typing.Any]) -> typing.Callable[..., typing.Any]:
    @functools.wraps(func)
    async def wrapper(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        ctx = None
        for arg in args:
            if isinstance(arg, context.NexusServiceContext):
                ctx = arg
                break

        if ctx is None:
            for arg_value in kwargs.values():
                if isinstance(arg_value, context.NexusServiceContext):
                    ctx = arg_value
                    break

        if ctx is None:
            raise exc.ServiceError(message="Transaction decorator requires a NexusServiceContext parameter")

        try:
            result = await func(*args, **kwargs)
            ctx.db.commit()
            return result
        except Exception as e:
            ctx.logger.error(f"Transaction failed, rolling back: {str(e)}")
            ctx.db.rollback()
            raise

    return wrapper
