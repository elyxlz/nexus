import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nexus.service.api.app import create_app
from nexus.service.core import exceptions as exc
from nexus.service.core.config import NexusServiceConfig
from nexus.service.core.context import NexusServiceContext
from nexus.service.core.db import create_connection
from nexus.service.core.logger import create_service_logger


@pytest.fixture
def git_tag():
    yield "blabla"


@pytest.fixture
def app_client() -> Iterator[TestClient]:
    config = NexusServiceConfig(
        service_dir=None,
        refresh_rate=1,
        host="localhost",
        port=54324,
        node_name="test_node",
        log_level="debug",
        mock_gpus=True,
    )

    _logger = create_service_logger(log_dir=None, name="nexus_test")
    _db = create_connection(_logger, ":memory:")
    context = NexusServiceContext(db=_db, config=config, logger=_logger)
    _app = create_app(ctx=context)

    with TestClient(_app) as client:
        time.sleep(0.1)
        _logger.info("Test client created with lifespan context")
        yield client


@pytest.fixture
def job_payload(git_tag) -> dict:
    return {
        "command": "echo 'Hello World'",
        "git_repo_url": "https://github.com/elyxlz/nexus.git",
        "git_tag": git_tag,
        "git_branch": "master",
        "user": "testuser",
        "discord_id": None,
    }


@pytest.fixture
def created_job(app_client: TestClient, job_payload: dict) -> dict:
    response = app_client.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    job = response.json()
    assert isinstance(job, dict)
    return job


def test_service_status(app_client: TestClient) -> None:
    response = app_client.get("/v1/service/status")
    assert response.status_code == 200
    data = response.json()
    assert "gpu_count" in data
    assert "service_version" in data
    assert "gpu_count" in data
    assert "queued_jobs" in data
    assert "running_jobs" in data
    assert "completed_jobs" in data
    assert "service_user" in data


def test_service_logs(app_client: TestClient) -> None:
    response = app_client.get("/v1/service/logs")
    assert response.status_code == 200
    data = response.json()
    assert "logs" in data


def test_add_job(app_client: TestClient, job_payload: dict) -> None:
    response = app_client.post("/v1/jobs", json=job_payload)
    assert response.status_code == 200
    job = response.json()
    assert isinstance(job, dict)
    assert job["command"] == "echo 'Hello World'"
    assert job["status"] == "queued"
    assert "id" in job
    assert job["git_repo_url"] == "https://github.com/elyxlz/nexus.git"
    assert job["git_tag"] == job_payload["git_tag"]
    assert job["user"] == "testuser"


def test_list_jobs(app_client: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]

    queued_resp = app_client.get("/v1/jobs", params={"status": "queued"})
    assert queued_resp.status_code == 200
    queued_jobs = queued_resp.json()
    assert any(job["id"] == job_id for job in queued_jobs)

    running_resp = app_client.get("/v1/jobs", params={"status": "running"})
    assert running_resp.status_code == 200
    assert isinstance(running_resp.json(), list)

    completed_resp = app_client.get("/v1/jobs", params={"status": "completed"})
    assert completed_resp.status_code == 200
    assert isinstance(completed_resp.json(), list)

    failed_resp = app_client.get("/v1/jobs", params={"status": "failed"})
    assert failed_resp.status_code == 200
    assert isinstance(failed_resp.json(), list)

    all_resp = app_client.get("/v1/jobs")
    assert all_resp.status_code == 200
    all_jobs = all_resp.json()
    assert any(job["id"] == job_id for job in all_jobs)


def test_list_jobs_by_gpu(app_client: TestClient) -> None:
    gpus_resp = app_client.get("/v1/gpus")
    assert gpus_resp.status_code == 200
    gpus = gpus_resp.json()
    assert len(gpus) > 0

    gpu_index = gpus[0]["index"]
    by_gpu_resp = app_client.get("/v1/jobs", params={"gpu_index": gpu_index})
    assert by_gpu_resp.status_code == 200
    assert isinstance(by_gpu_resp.json(), list)


def test_list_jobs_with_regex(app_client: TestClient, git_tag: str) -> None:
    job_payloads = [
        {
            "command": "python train.py --model=gpt2",
            "git_repo_url": "https://github.com/elyxlz/nexus.git",
            "git_tag": git_tag,
            "git_branch": "master",
            "user": "regex_test_user",
            "discord_id": None,
        },
        {
            "command": "python train.py --model=bert",
            "git_repo_url": "https://github.com/elyxlz/nexus.git",
            "git_tag": git_tag,
            "git_branch": "master",
            "user": "regex_test_user",
            "discord_id": None,
        },
        {
            "command": "python evaluate.py --model=gpt2",
            "git_repo_url": "https://github.com/elyxlz/nexus.git",
            "git_tag": git_tag,
            "git_branch": "master",
            "user": "regex_test_user",
            "discord_id": None,
        },
    ]

    job_ids = []
    for payload in job_payloads:
        response = app_client.post("/v1/jobs", json=payload)
        assert response.status_code == 200
        job = response.json()
        job_ids.append(job["id"])

    train_resp = app_client.get("/v1/jobs", params={"command_regex": "train\\.py"})
    assert train_resp.status_code == 200
    train_jobs = train_resp.json()
    assert len(train_jobs) >= 2
    train_commands = [job["command"] for job in train_jobs]
    assert all("train.py" in cmd for cmd in train_commands)

    gpt2_resp = app_client.get("/v1/jobs", params={"command_regex": "gpt2"})
    assert gpt2_resp.status_code == 200
    gpt2_jobs = gpt2_resp.json()
    assert len(gpt2_jobs) >= 2
    gpt2_commands = [job["command"] for job in gpt2_jobs]
    assert all("gpt2" in cmd for cmd in gpt2_commands)

    queued_gpt2_resp = app_client.get("/v1/jobs", params={"status": "queued", "command_regex": "gpt2"})
    assert queued_gpt2_resp.status_code == 200
    queued_gpt2_jobs = queued_gpt2_resp.json()
    queued_gpt2_commands = [job["command"] for job in queued_gpt2_jobs]
    assert all("gpt2" in cmd for cmd in queued_gpt2_commands)
    assert all(job["status"] == "queued" for job in queued_gpt2_jobs)


def test_get_job_details(app_client: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    response = app_client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200
    job = response.json()
    assert job["id"] == job_id
    assert job["status"] == "queued"
    assert job["command"] == created_job["command"]
    assert job["git_repo_url"] == created_job["git_repo_url"]
    assert job["git_tag"] == created_job["git_tag"]
    assert job["user"] == created_job["user"]


def test_get_job_logs(app_client: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]
    response = app_client.get(f"/v1/jobs/{job_id}/logs")
    assert response.status_code == 200
    data = response.json()
    assert "logs" in data
    assert isinstance(data["logs"], str)


def test_get_nonexistent_job(app_client: TestClient) -> None:
    with pytest.raises(exc.JobError) as excinfo:
        app_client.get("/v1/jobs/nonexistent")
    assert "Job not found: nonexistent" in str(excinfo.value)


def test_job_lifecycle(app_client: TestClient, git_tag: str) -> None:
    status_response = app_client.get("/v1/service/status")
    attempt = None
    assert status_response.status_code == 200
    assert "gpu_count" in status_response.json()

    job_payload = {
        "command": "echo 'Test job lifecycle'",
        "git_repo_url": "https://github.com/elyxlz/nexus.git",
        "git_tag": git_tag,
        "git_branch": "master",
        "user": "test_user",
        "discord_id": None,
    }

    submit_response = app_client.post("/v1/jobs", json=job_payload)
    assert submit_response.status_code == 200

    job = submit_response.json()
    job_id = job["id"]

    job_response = app_client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "queued"

    max_attempts = 40
    for attempt in range(max_attempts):
        job_response = app_client.get(f"/v1/jobs/{job_id}")
        assert job_response.status_code == 200
        job_data = job_response.json()

        if job_data["status"] != "queued":
            app_client.get("/v1/service/status")
            break

        if attempt % 5 == 0:
            print(f"Job {job_id} still queued after {attempt} attempts, waiting...")

        time.sleep(0.5)

    if attempt == max_attempts - 1 and job_data["status"] == "queued":
        pytest.fail("Job never left queued state")

    if job_data["status"] == "running":
        for attempt in range(max_attempts):
            job_response = app_client.get(f"/v1/jobs/{job_id}")
            assert job_response.status_code == 200
            job_data = job_response.json()

            if job_data["status"] in ["completed", "failed"]:
                break

            if attempt % 5 == 0:
                print(f"Job {job_id} still running after {attempt} attempts, waiting...")

            time.sleep(0.5)

    assert job_data["status"] in ["completed", "failed", "running"]

    logs_response = app_client.get(f"/v1/jobs/{job_id}/logs")
    assert logs_response.status_code == 200
    logs_data = logs_response.json()
    assert "logs" in logs_data


def test_job_error_handling(app_client: TestClient) -> None:
    with pytest.raises(exc.JobError) as excinfo:
        app_client.get("/v1/jobs/nonexistent-id")
    assert "Job not found" in str(excinfo.value)

    remove_response = app_client.request("DELETE", "/v1/jobs/queued", json=["nonexistent-id"])
    assert remove_response.status_code == 200

    invalid_job = {
        "command": "echo 'Invalid job'",
        "git_repo_url": "not-a-valid-url",
        "git_tag": "main",
        "git_branch": "main",
        "user": "test_user",
        "discord_id": None,
    }

    with pytest.raises(exc.GitError) as excinfo:
        app_client.post("/v1/jobs", json=invalid_job)
    assert "Invalid git repository URL" in str(excinfo.value)


def test_blacklist_and_remove_gpu(app_client: TestClient) -> None:
    resp = app_client.get("/v1/gpus")
    assert resp.status_code == 200
    gpus = resp.json()
    assert len(gpus) > 0
    gpu_index = gpus[0]["index"]

    app_client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])

    blacklist_resp = app_client.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp.status_code == 200
    bl_data = blacklist_resp.json()
    assert gpu_index in bl_data.get("blacklisted", [])

    resp_after_blacklist = app_client.get("/v1/gpus")
    assert resp_after_blacklist.status_code == 200
    gpus_after = resp_after_blacklist.json()
    blacklisted_gpu = next((g for g in gpus_after if g["index"] == gpu_index), None)
    assert blacklisted_gpu is not None
    assert blacklisted_gpu["is_blacklisted"] is True

    blacklist_resp2 = app_client.post("/v1/gpus/blacklist", json=[gpu_index])
    assert blacklist_resp2.status_code == 200
    bl_data2 = blacklist_resp2.json()
    assert any(item.get("index") == gpu_index for item in bl_data2.get("failed", []))

    remove_resp = app_client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert gpu_index in rem_data.get("removed", [])

    resp_after_removal = app_client.get("/v1/gpus")
    assert resp_after_removal.status_code == 200
    gpus_after_removal = resp_after_removal.json()
    non_blacklisted_gpu = next((g for g in gpus_after_removal if g["index"] == gpu_index), None)
    assert non_blacklisted_gpu is not None
    assert non_blacklisted_gpu["is_blacklisted"] is False

    remove_resp2 = app_client.request("DELETE", "/v1/gpus/blacklist", json=[gpu_index])
    assert remove_resp2.status_code == 200
    rem_data2 = remove_resp2.json()
    assert any(item.get("index") == gpu_index for item in rem_data2.get("failed", []))


def test_kill_running_job(app_client: TestClient, git_tag: str) -> None:
    job_payload = {
        "command": "sleep 30",
        "git_repo_url": "https://github.com/elyxlz/nexus.git",
        "git_tag": git_tag,
        "git_branch": "master",
        "user": "test_user",
        "discord_id": None,
    }

    submit_response = app_client.post("/v1/jobs", json=job_payload)
    assert submit_response.status_code == 200
    job_id = submit_response.json()["id"]

    max_attempts = 20
    for attempt in range(max_attempts):
        job_response = app_client.get(f"/v1/jobs/{job_id}")
        job_data = job_response.json()
        if job_data["status"] == "running":
            break
        time.sleep(0.5)

    job_data = app_client.get(f"/v1/jobs/{job_id}").json()
    if job_data["status"] != "running":
        pytest.skip("Test requires the job to reach running state")

    kill_response = app_client.request("DELETE", "/v1/jobs/running", json=[job_id])
    assert kill_response.status_code == 200
    kill_data = kill_response.json()
    assert job_id in kill_data.get("killed", [])

    for attempt in range(max_attempts):
        job_response = app_client.get(f"/v1/jobs/{job_id}")
        job_data = job_response.json()
        if job_data["status"] == "failed":
            break
        time.sleep(0.5)

    job_response = app_client.get(f"/v1/jobs/{job_id}")
    job_data = job_response.json()
    assert job_data["status"] == "failed"
    assert "Killed by user" in job_data.get("error_message", "")


def test_remove_queued_jobs(app_client: TestClient, created_job: dict) -> None:
    job_id = created_job["id"]

    job_response = app_client.get(f"/v1/jobs/{job_id}")
    assert job_response.json()["status"] == "queued"

    remove_resp = app_client.request("DELETE", "/v1/jobs/queued", json=[job_id])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert job_id in rem_data.get("removed", [])

    list_resp = app_client.get("/v1/jobs", params={"status": "queued"})
    assert list_resp.status_code == 200
    queued_jobs = list_resp.json()
    assert not any(job["id"] == job_id for job in queued_jobs)

    with pytest.raises(exc.JobError) as excinfo:
        app_client.get(f"/v1/jobs/{job_id}")
    assert "Job not found" in str(excinfo.value)


def test_remove_nonexistent_queued_job(app_client: TestClient) -> None:
    remove_resp = app_client.request("DELETE", "/v1/jobs/queued", json=["nonexistent"])
    assert remove_resp.status_code == 200
    rem_data = remove_resp.json()
    assert "nonexistent" not in rem_data.get("removed", [])


def test_service_stop(app_client: TestClient) -> None:
    response = app_client.post("/v1/service/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopping"
