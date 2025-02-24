import dataclasses
import pathlib as pl

from nexus.service.core.db import (
    add_blacklisted_gpu,
    add_job,
    connect,
    delete_queued_job,
    get_job,
    list_blacklisted_gpus,
    list_jobs,
    remove_blacklisted_gpu,
    update_job,
)
from nexus.service.job import create_job


def test_add_and_get_job(tmp_path: pl.Path):
    # Create a temporary database file and initialize tables.
    db_path = tmp_path / "test.db"
    conn = connect(str(db_path))

    # Create a new job using the job creation helper.
    job = create_job(
        "echo 'Hello World'",
        "https://github.com/elyxlz/nexus",
        "main",
        "testuser",
        None,
    )
    # Add the job and commit.
    add_job(conn, job)
    conn.commit()

    # Retrieve the job from the DB and compare.
    retrieved = get_job(conn, job.id)
    assert retrieved is not None
    # Compare via asdict for equality.
    assert dataclasses.asdict(retrieved) == dataclasses.asdict(job)
    conn.close()


def test_update_job(tmp_path: pl.Path):
    db_path = tmp_path / "test.db"
    conn = connect(str(db_path))

    job = create_job(
        "echo 'Initial Command'",
        "https://github.com/elyxlz/nexus",
        "main",
        "user1",
        None,
    )
    add_job(conn, job)
    conn.commit()

    # Create an updated job instance (e.g. change status to "running").
    updated_job = job.__class__(**{**job.__dict__, "status": "running"})
    update_job(conn, updated_job)
    conn.commit()

    retrieved = get_job(conn, job.id)
    assert retrieved is not None
    assert retrieved.status == "running"
    conn.close()


def test_list_and_delete_jobs(tmp_path: pl.Path):
    db_path = tmp_path / "test.db"
    conn = connect(str(db_path))

    # Create two jobs.
    job1 = create_job("echo 'Job1'", "https://github.com/elyxlz/nexus", "main", "user1", None)
    job2 = create_job("echo 'Job2'", "https://github.com/elyxlz/nexus", "main", "user2", None)
    # For testing purposes, update job2 status to "running" (so it is not queued).
    job2 = job2.__class__(**{**job2.__dict__, "status": "running"})

    add_job(conn, job1)
    add_job(conn, job2)
    conn.commit()

    queued_jobs = list_jobs(conn, status="queued")
    running_jobs = list_jobs(conn, status="running")
    completed_jobs = list_jobs(conn, status="completed")

    assert any(j.id == job1.id for j in queued_jobs)
    assert any(j.id == job2.id for j in running_jobs)
    # Expect no completed jobs yet.
    assert completed_jobs == []

    # Delete the queued job (job1) and verify deletion.
    success = delete_queued_job(conn, job1.id)
    assert success is True
    conn.commit()

    # Now job1 should not be found.
    retrieved = get_job(conn, job1.id)
    assert retrieved is None

    # Attempting to delete a non-queued job (job2) should return False.
    success2 = delete_queued_job(conn, job2.id)
    assert success2 is False
    conn.close()


def test_blacklisted_gpus(tmp_path: pl.Path):
    db_path = tmp_path / "test.db"
    conn = connect(str(db_path))

    # Initially, no GPUs should be blacklisted.
    bl = list_blacklisted_gpus(conn)
    assert bl == []

    # Add GPU index 0 to the blacklist.
    added = add_blacklisted_gpu(conn, 0)
    assert added is True
    conn.commit()

    bl = list_blacklisted_gpus(conn)
    assert 0 in bl

    # Adding the same GPU again should return False.
    added_again = add_blacklisted_gpu(conn, 0)
    assert added_again is False

    # Remove the GPU from the blacklist.
    removed = remove_blacklisted_gpu(conn, 0)
    assert removed is True
    conn.commit()

    bl = list_blacklisted_gpus(conn)
    assert 0 not in bl

    # Removing a GPU that is not blacklisted should return False.
    removed_again = remove_blacklisted_gpu(conn, 0)
    assert removed_again is False
    conn.close()
