import pytest
from fastapi.testclient import TestClient

from nexus.service.main import app


@pytest.fixture(scope="session")
def client():
    # You can mock configuration or state here if needed
    # For simplicity, we use the app as is.
    with TestClient(app) as c:
        yield c


def test_service_status(client):
    resp = client.get("/v1/service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is True
    assert "service_version" in data


def test_add_jobs(client):
    payload = {
        "commands": ["echo 'Hello World'"],
        "git_repo_url": "https://github.com/elyxlz/nexus",
        "git_tag": "main",
        "user": "testuser",
        "discord_id": None,
    }
    resp = client.post("/v1/jobs", json=payload)
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["command"] == "echo 'Hello World'"
    assert job["status"] == "queued"
    global TEST_JOB_ID
    TEST_JOB_ID = job["id"]


def test_list_jobs(client):
    # Check queued jobs
    resp = client.get("/v1/jobs", params={"status": "queued"})
    assert resp.status_code == 200
    jobs = resp.json()
    assert any(j["id"] == TEST_JOB_ID for j in jobs)

    # Check running, completed, etc. - They might be empty at this point
    resp = client.get("/v1/jobs", params={"status": "running"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    resp = client.get("/v1/jobs", params={"status": "completed"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_get_job_details(client):
    resp = client.get(f"/v1/jobs/{TEST_JOB_ID}")
    assert resp.status_code == 200
    job = resp.json()
    assert job["id"] == TEST_JOB_ID
    assert job["status"] == "queued"


def test_get_job_logs(client):
    resp = client.get(f"/v1/jobs/{TEST_JOB_ID}/logs")
    # Might not have logs yet since it hasn't run
    # Should still return a valid response
    assert resp.status_code == 200
    logs = resp.json()["logs"]
    # Logs could be empty at this stage
    assert isinstance(logs, str)


def test_blacklist_gpu(client):
    # List GPUs first (may be mock or empty)
    resp = client.get("/v1/gpus")
    assert resp.status_code == 200
    gpus = resp.json()

    if len(gpus) > 0:
        # Blacklist the first GPU
        gpu_index = gpus[0]["index"]
        resp = client.post("/v1/gpus/blacklist", json=[gpu_index])
        assert resp.status_code == 200
        data = resp.json()
        assert gpu_index in data.get("blacklisted", [])

        # Remove from blacklist using request() instead of delete()
        resp = client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
        assert resp.status_code == 200
        data = resp.json()
        assert gpu_index in data.get("removed", [])


def test_remove_queued_jobs(client):
    # Remove the previously added job if it's still queued
    resp = client.request("DELETE", "/v1/jobs/queued", json=[TEST_JOB_ID])
    assert resp.status_code == 200
    data = resp.json()
    assert TEST_JOB_ID in data["removed"]

    # Verify it's gone
    resp = client.get("/v1/jobs", params={"status": "queued"})
    assert resp.status_code == 200
    jobs = resp.json()
    assert not any(j["id"] == TEST_JOB_ID for j in jobs)


def test_service_stop(client):
    # Attempt stopping the service
    # This should return 200 and status=stopping
    resp = client.post("/v1/service/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopping"
