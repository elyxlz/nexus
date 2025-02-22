import pathlib as pl
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus.service.config import NexusServiceConfig
from nexus.service.main import create_app
from nexus.service.models import NexusServiceState

# Create mock state and config for testing.
mock_state = NexusServiceState(status="running", jobs=(), blacklisted_gpus=())
mock_config = NexusServiceConfig(
    service_dir=pl.Path("./nexus_tests"),
    refresh_rate=5,
    history_limit=1000,
    host="localhost",
    port=54324,
    webhooks_enabled=False,
    node_name="test_node",
    log_level="debug",
    mock_gpus=True,
    persist_to_disk=False,
)

app = create_app(custom_state=mock_state, custom_config=mock_config)


# Fixture to provide the TestClient instance.
@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def job_payload() -> dict[str, Any]:
    return {
        "commands": ["echo 'Hello World'"],
        "git_repo_url": "https://github.com/elyxlz/nexus",
        "git_tag": "main",
        "user": "testuser",
        "discord_id": None,
    }


@pytest.fixture
def created_job(client: TestClient, job_payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    jobs: list[dict[str, Any]] = response.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 1
    return jobs[0]


def test_service_status(client: TestClient) -> None:
    response = client.get("/v1/service/status")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["running"] is True
    assert "service_version" in data


def test_add_job(client: TestClient, job_payload: dict[str, Any]) -> None:
    response = client.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    jobs: list[dict[str, Any]] = response.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 1
    job: dict[str, Any] = jobs[0]
    assert job["command"] == "echo 'Hello World'"
    assert job["status"] == "queued"
    # Check that a unique job ID is provided.
    assert "id" in job


def test_list_jobs(client: TestClient, created_job: dict[str, Any]) -> None:
    job_id: str = created_job["id"]

    # Verify the created job appears in the queued jobs list.
    queued_resp = client.get("/v1/jobs", params={"status": "queued"})
    assert queued_resp.status_code == 200
    queued_jobs: list[dict[str, Any]] = queued_resp.json()
    assert any(job["id"] == job_id for job in queued_jobs)

    # Verify that endpoints for running and completed jobs return valid lists.
    running_resp = client.get("/v1/jobs", params={"status": "running"})
    assert running_resp.status_code == 200
    assert isinstance(running_resp.json(), list)

    completed_resp = client.get("/v1/jobs", params={"status": "completed"})
    assert completed_resp.status_code == 200
    assert isinstance(completed_resp.json(), list)


def test_get_job_details(client: TestClient, created_job: dict[str, Any]) -> None:
    job_id: str = created_job["id"]
    response = client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200
    job: dict[str, Any] = response.json()
    assert job["id"] == job_id
    assert job["status"] == "queued"


def test_get_job_logs(client: TestClient, created_job: dict[str, Any]) -> None:
    job_id: str = created_job["id"]
    response = client.get(f"/v1/jobs/{job_id}/logs")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    # Even if logs are empty, they should be returned as a string.
    assert "logs" in data
    assert isinstance(data["logs"], str)


def test_get_nonexistent_job(client: TestClient) -> None:
    response = client.get("/v1/jobs/nonexistent")
    assert response.status_code == 404


def test_blacklist_and_remove_gpu(client: TestClient) -> None:
    resp = client.get("/v1/gpus")
    assert resp.status_code == 200
    gpus: list[dict[str, Any]] = resp.json()
    gpu_index: int = gpus[0]["index"]

    # Ensure the GPU is not already blacklisted.
    client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])

    # Blacklist the GPU.
    blacklist_resp = client.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp.status_code == 200
    bl_data: dict[str, Any] = blacklist_resp.json()
    assert gpu_index in bl_data.get("blacklisted", [])

    # Attempt to blacklist the same GPU again.
    blacklist_resp2 = client.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp2.status_code == 200
    bl_data2: dict[str, Any] = blacklist_resp2.json()
    assert any(item.get("index") == gpu_index for item in bl_data2.get("failed", []))

    # Remove the GPU from the blacklist.
    remove_resp = client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp.status_code == 200
    rem_data: dict[str, Any] = remove_resp.json()
    assert gpu_index in rem_data.get("removed", [])

    # Attempt to remove the same GPU again.
    remove_resp2 = client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp2.status_code == 200
    rem_data2: dict[str, Any] = remove_resp2.json()
    assert any(item.get("index") == gpu_index for item in rem_data2.get("failed", []))


def test_remove_queued_jobs(client: TestClient, created_job: dict[str, Any]) -> None:
    job_id: str = created_job["id"]
    remove_resp = client.request("DELETE", "/v1/jobs/queued", json=[job_id])
    assert remove_resp.status_code == 200
    rem_data: dict[str, Any] = remove_resp.json()
    assert job_id in rem_data.get("removed", [])
    list_resp = client.get("/v1/jobs", params={"status": "queued"})
    assert list_resp.status_code == 200
    queued_jobs: list[dict[str, Any]] = list_resp.json()
    assert not any(job["id"] == job_id for job in queued_jobs)


def test_remove_nonexistent_queued_job(client: TestClient) -> None:
    remove_resp = client.request("DELETE", "/v1/jobs/queued", json=["nonexistent"])
    assert remove_resp.status_code == 200
    rem_data: dict[str, Any] = remove_resp.json()
    # Since the job doesn't exist, it should not appear in the removed list.
    assert "nonexistent" not in rem_data.get("removed", [])


def test_service_stop(client: TestClient) -> None:
    response = client.post("/v1/service/stop")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["status"] == "stopping"
