import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nexus.service.core.config import NexusServiceConfig
from nexus.service.core.context import NexusServiceContext
from nexus.service.core.db import create_tables
from nexus.service.core.env import NexusServiceEnv
from nexus.service.core.logger import NexusServiceLogger, create_service_logger
from nexus.service.main import create_app


@pytest.fixture
def mock_logger() -> NexusServiceLogger:
    return create_service_logger(log_dir=None, name="nexus_test")


# Fixture to create an in-memory SQLite database and initialize tables.
@pytest.fixture
def test_db(mock_logger) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    create_tables(_logger=mock_logger, conn=connection)
    yield connection
    connection.close()


# Fixture to create a mock context using the in-memory database and service_dir=None.
@pytest.fixture
def mock_context(test_db: sqlite3.Connection) -> NexusServiceContext:
    # service_dir is set to None as requested.
    mock_config = NexusServiceConfig(
        service_dir=None,
        refresh_rate=5,
        host="localhost",
        port=54324,
        webhooks_enabled=False,
        node_name="test_node",
        log_level="debug",
        mock_gpus=True,
    )
    mock_env = NexusServiceEnv()
    mock_logger = create_service_logger(log_dir=None, name="nexus_test")
    return NexusServiceContext(db=test_db, config=mock_config, env=mock_env, logger=mock_logger)


# Fixture to create the FastAPI app with the custom context.
@pytest.fixture
def app(mock_context: NexusServiceContext) -> TestClient:
    app_instance = create_app(ctx=mock_context)
    return TestClient(app_instance)


@pytest.fixture
def job_payload() -> dict:
    return {
        "commands": ["echo 'Hello World'"],
        "git_repo_url": "https://github.com/elyxlz/nexus",
        "git_tag": "main",
        "user": "testuser",
        "discord_id": None,
    }


@pytest.fixture
def created_job(app: TestClient, job_payload: dict) -> dict:
    response = app.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 1
    return jobs[0]


def test_service_status(app: TestClient) -> None:
    response = app.get("/v1/service/status")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True
    assert "service_version" in data


def test_add_job(app: TestClient, job_payload: dict) -> None:
    response = app.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["command"] == "echo 'Hello World'"
    assert job["status"] == "queued"
    assert "id" in job


def test_list_jobs(app: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    queued_resp = app.get("/v1/jobs", params={"status": "queued"})
    assert queued_resp.status_code == 200
    queued_jobs = queued_resp.json()
    assert any(job["id"] == job_id for job in queued_jobs)

    running_resp = app.get("/v1/jobs", params={"status": "running"})
    assert running_resp.status_code == 200
    assert isinstance(running_resp.json(), list)

    completed_resp = app.get("/v1/jobs", params={"status": "completed"})
    assert completed_resp.status_code == 200
    assert isinstance(completed_resp.json(), list)


def test_get_job_details(app: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    response = app.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200
    job = response.json()
    assert job["id"] == job_id
    assert job["status"] == "queued"


def test_get_job_logs(app: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    response = app.get(f"/v1/jobs/{job_id}/logs")
    assert response.status_code == 200
    data = response.json()
    assert "logs" in data
    assert isinstance(data["logs"], str)


def test_get_nonexistent_job(app: TestClient) -> None:
    import pytest

    from nexus.service.core import exceptions as exc

    # Using pytest.raises to catch the expected exception
    with pytest.raises(exc.JobError) as excinfo:
        app.get("/v1/jobs/nonexistent")

    # Verify the error message
    assert "Job not found: nonexistent" in str(excinfo.value)


def test_blacklist_and_remove_gpu(app: TestClient) -> None:
    resp = app.get("/v1/gpus")
    assert resp.status_code == 200
    gpus = resp.json()
    gpu_index = gpus[0]["index"]

    # Ensure the GPU is not already blacklisted.
    app.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])

    # Blacklist the GPU.
    blacklist_resp = app.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp.status_code == 200
    bl_data = blacklist_resp.json()
    assert gpu_index in bl_data.get("blacklisted", [])

    # Attempt to blacklist the same GPU again.
    blacklist_resp2 = app.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp2.status_code == 200
    bl_data2 = blacklist_resp2.json()
    assert any(item.get("index") == gpu_index for item in bl_data2.get("failed", []))

    # Remove the GPU from the blacklist.
    remove_resp = app.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert gpu_index in rem_data.get("removed", [])

    # Attempt to remove the same GPU again.
    remove_resp2 = app.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp2.status_code == 200
    rem_data2 = remove_resp2.json()
    assert any(item.get("index") == gpu_index for item in rem_data2.get("failed", []))


def test_remove_queued_jobs(app: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    remove_resp = app.request("DELETE", "/v1/jobs/queued", json=[job_id])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert job_id in rem_data.get("removed", [])
    list_resp = app.get("/v1/jobs", params={"status": "queued"})
    assert list_resp.status_code == 200
    queued_jobs = list_resp.json()
    assert not any(job["id"] == job_id for job in queued_jobs)


def test_remove_nonexistent_queued_job(app: TestClient) -> None:
    remove_resp = app.request("DELETE", "/v1/jobs/queued", json=["nonexistent"])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert "nonexistent" not in rem_data.get("removed", [])


def test_service_stop(app: TestClient) -> None:
    response = app.post("/v1/service/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopping"
