import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from nexus.server.api.app import create_app
from nexus.server.core.config import NexusServerConfig
from nexus.server.core.context import NexusServerContext
from nexus.server.core.db import create_connection

# Use a fixed file for the test database
DB_FILE = "test_persistence.db"


@pytest.fixture(scope="function")
def db_path(tmp_path) -> str:
    """Create a new test database for each test."""
    db_path = str(tmp_path / DB_FILE)
    return db_path


@pytest.fixture
def server_config() -> NexusServerConfig:
    """Create server configuration for testing."""
    return NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54325,
        node_name="test_persistence_node",
        mock_gpus=True,
    )


@pytest.fixture
def create_test_client(db_path: str, server_config: NexusServerConfig) -> Callable[[], TestClient]:
    """Factory fixture to create test clients with the same DB path."""

    def _create_client() -> TestClient:
        # Use the same DB path for all test clients
        print(f"Using database at: {db_path}")
        db = create_connection(db_path)
        context = NexusServerContext(db=db, config=server_config)
        app = create_app(ctx=context)

        client = TestClient(app)
        time.sleep(0.2)  # Allow server to initialize
        return client

    return _create_client


@pytest.fixture
def artifact_id() -> str:
    """Create a test artifact ID."""
    return "test_artifact_123"


@pytest.fixture
def job_payload() -> dict:
    """Create a test job payload."""
    return {
        "command": "echo 'Persistence Test'",
        "git_repo_url": "https://github.com/elyxlz/nexus.git",
        "git_branch": "master",
        "user": "persistence_test_user",
        "discord_id": None,
        "num_gpus": 1,
        "env": {"TEST_VAR": "test_value"},
        "jobrc": None,
        "priority": 0,
        "search_wandb": False,
        "notifications": [],
    }


@pytest.fixture
def artifact_data():
    """Create test artifact data."""
    # Create simple tar file data
    import io
    import tarfile

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        # Create a simple file in the archive
        info = tarfile.TarInfo("README.md")
        data = b"# Test Repository\nThis is a test tar archive for persistence testing."
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    return output.getvalue()


def add_artifact_to_db(client, artifact_data) -> str:
    """Upload an artifact and return its ID."""
    files = {"file": ("archive.tar.gz", artifact_data, "application/gzip")}
    response = client.post("/v1/artifacts", files=files)
    assert response.status_code == 201
    return response.json()["data"]


def test_api_with_persistent_database(
    create_test_client: Callable[[], TestClient],
    job_payload: dict,
    artifact_data: bytes,
    db_path: str,
) -> None:
    """Test the API endpoints use a persistent database."""
    # Create a client with the database
    client = create_test_client()

    # Add artifact to the database and get its ID
    artifact_id = add_artifact_to_db(client, artifact_data)
    
    # Update job payload with the generated artifact ID
    test_payload = {**job_payload, "artifact_id": artifact_id}

    # Submit a job
    submit_response = client.post("/v1/jobs", json=test_payload)
    assert submit_response.status_code == 201
    job_data = submit_response.json()
    job_id = job_data["id"]

    # Check the job was created
    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["status"] == "queued"
    assert job["command"] == job_payload["command"]

    # Submit another job
    job_payload2 = dict(test_payload)
    job_payload2["command"] = "echo 'Second job'"
    submit_response2 = client.post("/v1/jobs", json=job_payload2)
    assert submit_response2.status_code == 201
    job_id2 = submit_response2.json()["id"]

    # Check both jobs exist
    jobs = client.get("/v1/jobs").json()
    assert len(jobs) == 2
    assert any(j["id"] == job_id for j in jobs)
    assert any(j["id"] == job_id2 for j in jobs)

    # No need to stop server


def test_gpu_blacklisting(create_test_client: Callable[[], TestClient], db_path: str) -> None:
    """Test the GPU blacklisting endpoint."""
    # Create a client
    client = create_test_client()

    # Get available GPUs
    gpus_response = client.get("/v1/gpus")
    assert gpus_response.status_code == 200
    gpus = gpus_response.json()
    assert len(gpus) > 0
    gpu_idx = gpus[0]["index"]

    # Blacklist a GPU
    blacklist_response = client.put(f"/v1/gpus/{gpu_idx}/blacklist")
    assert blacklist_response.status_code == 200
    blacklist_data = blacklist_response.json()
    assert blacklist_data["gpu_idx"] == gpu_idx
    assert blacklist_data["blacklisted"] is True

    # Verify GPU is now blacklisted
    gpus_after = client.get("/v1/gpus").json()
    blacklisted_gpu = next((g for g in gpus_after if g["index"] == gpu_idx), None)
    assert blacklisted_gpu is not None
    assert blacklisted_gpu["is_blacklisted"] is True

    # Remove from blacklist
    remove_response = client.delete(f"/v1/gpus/{gpu_idx}/blacklist")
    assert remove_response.status_code == 200
    remove_data = remove_response.json()
    assert remove_data["gpu_idx"] == gpu_idx
    assert remove_data["blacklisted"] is False

    # Verify GPU is no longer blacklisted
    gpus_after_removal = client.get("/v1/gpus").json()
    non_blacklisted_gpu = next((g for g in gpus_after_removal if g["index"] == gpu_idx), None)
    assert non_blacklisted_gpu is not None
    assert non_blacklisted_gpu["is_blacklisted"] is False

    # No need to stop server


def test_job_lifecycle(
    create_test_client: Callable[[], TestClient],
    job_payload: dict,
    artifact_data: bytes,
    db_path: str,
) -> None:
    """Test basic job lifecycle functionality."""
    # Create a client
    client = create_test_client()

    # Add artifact to the database and get its ID
    artifact_id = add_artifact_to_db(client, artifact_data)
    
    # Update job payload with the generated artifact ID
    test_payload = {**job_payload, "artifact_id": artifact_id}

    # Submit a job
    submit_response = client.post("/v1/jobs", json=test_payload)
    assert submit_response.status_code == 201
    job_id = submit_response.json()["id"]

    # Check job status - should be queued
    job = client.get(f"/v1/jobs/{job_id}").json()
    assert job["status"] == "queued"

    # List jobs by status
    queued_jobs = client.get("/v1/jobs", params={"status": "queued"}).json()
    assert any(j["id"] == job_id for j in queued_jobs)

    running_jobs = client.get("/v1/jobs", params={"status": "running"}).json()
    assert not any(j["id"] == job_id for j in running_jobs)

    # Try to delete the job
    response = client.delete(f"/v1/jobs/{job_id}")
    assert response.status_code == 204

    # Verify job is gone
    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 404

    # No need to stop server
