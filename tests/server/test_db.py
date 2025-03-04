import dataclasses
import pathlib as pl

import pytest

from nexus.server.core import exceptions as exc
from nexus.server.core.db import (
    add_blacklisted_gpu,
    add_job,
    create_connection,
    delete_queued_job,
    get_job,
    list_blacklisted_gpus,
    list_jobs,
    remove_blacklisted_gpu,
    update_job,
)
from nexus.server.core.job import create_job
from nexus.server.core.logger import NexusServerLogger, create_logger


@pytest.fixture
def mock_logger() -> NexusServerLogger:
    return create_logger(log_dir=None, name="nexus_test")


def test_add_and_get_job(tmp_path: pl.Path, mock_logger: NexusServerLogger):
    db_path = tmp_path / "test.db"
    conn = create_connection(mock_logger, db_path=str(db_path))

    job = create_job(
        "echo 'Hello World'",
        git_repo_url="https://github.com/elyxlz/nexus",
        git_tag="main",
        git_branch="blah",
        user="testuser",
        num_gpus=1,
        node_name="xx",
        env={},
        jobrc=None,
        priority=0,
        search_wandb=False,
        notifications=[],
    )
    add_job(mock_logger, conn=conn, job=job)
    conn.commit()

    retrieved = get_job(mock_logger, conn=conn, job_id=job.id)
    assert retrieved is not None
    assert dataclasses.asdict(retrieved) == dataclasses.asdict(job)
    conn.close()


def test_update_job(tmp_path: pl.Path, mock_logger: NexusServerLogger):
    db_path = tmp_path / "test.db"
    conn = create_connection(mock_logger, db_path=str(db_path))

    job = create_job(
        "echo 'Initial Command'",
        git_repo_url="https://github.com/elyxlz/nexus",
        git_tag="main",
        git_branch="blah",
        user="testuser",
        node_name="xx",
        num_gpus=1,
        env={},
        jobrc=None,
        priority=0,
        search_wandb=False,
        notifications=[],
    )
    add_job(mock_logger, conn=conn, job=job)
    conn.commit()

    updated_job = job.__class__(**{**job.__dict__, "status": "running"})
    update_job(mock_logger, conn=conn, job=updated_job)
    conn.commit()

    retrieved = get_job(mock_logger, conn=conn, job_id=job.id)
    assert retrieved is not None
    assert retrieved.status == "running"
    conn.close()


def test_list_and_delete_jobs(tmp_path: pl.Path, mock_logger: NexusServerLogger):
    db_path = tmp_path / "test.db"
    conn = create_connection(mock_logger, db_path=str(db_path))

    job1 = create_job(
        "echo 'Job1'",
        git_repo_url="https://github.com/elyxlz/nexus",
        git_tag="main",
        git_branch="test",
        user="user1",
        node_name="test",
        num_gpus=1,
        env={},
        jobrc=None,
        priority=0,
        search_wandb=False,
        notifications=[],
    )
    job2 = create_job(
        "echo 'Job2'",
        git_repo_url="https://github.com/elyxlz/nexus",
        git_tag="main",
        git_branch="test",
        user="user1",
        node_name="test",
        num_gpus=1,
        env={},
        jobrc=None,
        priority=0,
        search_wandb=False,
        notifications=[],
    )
    job2 = job2.__class__(**{**job2.__dict__, "status": "running"})

    add_job(mock_logger, conn=conn, job=job1)
    add_job(mock_logger, conn=conn, job=job2)
    conn.commit()

    queued_jobs = list_jobs(mock_logger, conn=conn, status="queued")
    running_jobs = list_jobs(mock_logger, conn=conn, status="running")
    completed_jobs = list_jobs(mock_logger, conn=conn, status="completed")

    assert any(j.id == job1.id for j in queued_jobs)
    assert any(j.id == job2.id for j in running_jobs)
    assert completed_jobs == []

    success = delete_queued_job(mock_logger, conn=conn, job_id=job1.id)
    assert success is True
    conn.commit()

    retrieved = get_job(mock_logger, conn=conn, job_id=job1.id)
    assert retrieved is None

    with pytest.raises(exc.JobError):
        delete_queued_job(mock_logger, conn=conn, job_id=job2.id)
    conn.close()


def test_blacklisted_gpus(tmp_path: pl.Path, mock_logger: NexusServerLogger):
    db_path = tmp_path / "test.db"
    conn = create_connection(mock_logger, db_path=str(db_path))

    bl = list_blacklisted_gpus(mock_logger, conn=conn)
    assert bl == []

    added = add_blacklisted_gpu(mock_logger, conn=conn, gpu_idx=0)
    assert added is True
    conn.commit()

    bl = list_blacklisted_gpus(mock_logger, conn=conn)
    assert 0 in bl

    added_again = add_blacklisted_gpu(mock_logger, conn=conn, gpu_idx=0)
    assert added_again is False

    removed = remove_blacklisted_gpu(mock_logger, conn=conn, gpu_idx=0)
    assert removed is True
    conn.commit()

    bl = list_blacklisted_gpus(mock_logger, conn=conn)
    assert 0 not in bl

    removed_again = remove_blacklisted_gpu(mock_logger, conn=conn, gpu_idx=0)
    assert removed_again is False
    conn.close()
