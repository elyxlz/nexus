import pathlib as pl
import sqlite3

from nexus.service.core import models


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
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


def add_job(conn: sqlite3.Connection, job: models.Job) -> None:
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


def update_job(conn: sqlite3.Connection, job: models.Job) -> None:
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


def get_job(conn: sqlite3.Connection, job_id: str) -> models.Job | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if row:
        return row_to_job(row)
    return None


def list_jobs(conn: sqlite3.Connection, status: str | None = None) -> list[models.Job]:
    cur = conn.cursor()
    if status is not None:
        cur.execute("SELECT * FROM jobs WHERE status = ?", (status,))
    else:
        cur.execute("SELECT * FROM jobs")
    rows = cur.fetchall()
    return [row_to_job(row) for row in rows]


def delete_queued_job(conn: sqlite3.Connection, job_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row or row["status"] != "queued":
        return False
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return True


def add_blacklisted_gpu(conn: sqlite3.Connection, gpu_index: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    if cur.fetchone():
        return False
    cur.execute("INSERT INTO blacklisted_gpus (gpu_index) VALUES (?)", (gpu_index,))
    return True


def remove_blacklisted_gpu(conn: sqlite3.Connection, gpu_index: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM blacklisted_gpus WHERE gpu_index = ?", (gpu_index,))
    return True


def list_blacklisted_gpus(conn: sqlite3.Connection) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT gpu_index FROM blacklisted_gpus")
    rows = cur.fetchall()
    return [row["gpu_index"] for row in rows]
