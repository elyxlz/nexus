import time
import dataclasses as dc
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nexus.server.api.app import create_app
from nexus.server.core.config import NexusServerConfig
from nexus.server.core.context import NexusServerContext
from nexus.server.core.db import create_connection
from nexus.server.api import scheduler


def upload_test_artifact(client: TestClient, artifact_data: bytes) -> str:
    """Upload an artifact and return its ID."""
    response = client.post("/v1/artifacts", content=artifact_data)
    assert response.status_code == 201
    return response.json()["data"]


@pytest.fixture(scope="module")
def multinode_db():
    """Create a dedicated database for all multi-node tests.

    This creates an independent database instance on a unique port.
    """
    # Import the setup function from conftest
    from conftest import setup_test_db

    # Set up a database on a unique port for these tests
    # Use a higher port number to avoid conflicts
    return setup_test_db(port=5322)


@pytest.fixture
def node1_config(multinode_db) -> NexusServerConfig:
    """Configuration for the first node in multi-node tests."""
    return NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54328,
        node_name="node1",
        mock_gpus=True,
        api_key="test_api_key",
        rqlite_host=multinode_db,
    )


@pytest.fixture
def node2_config(multinode_db) -> NexusServerConfig:
    """Configuration for the second node in multi-node tests."""
    return NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54329,
        node_name="node2",
        mock_gpus=True,
        api_key="test_api_key",
        rqlite_host=multinode_db,
    )


@pytest.fixture
def artifact_data():
    # Create simple tar file data
    import io
    import tarfile

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        # Create a simple file in the archive
        info = tarfile.TarInfo("README.md")
        data = b"# Test Repository\nThis is a test tar archive for testing."
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    return output.getvalue()


@pytest.fixture
def node1_client(node1_config: NexusServerConfig) -> Iterator[tuple[TestClient, NexusServerContext]]:
    host, port = node1_config.rqlite_host.split(":")
    db = create_connection(host, int(port), node1_config.api_key)
    context = NexusServerContext(db=db, config=node1_config)
    app = create_app(ctx=context)

    with TestClient(app) as client:
        time.sleep(0.1)
        yield client, context


@pytest.fixture
def node2_client(node2_config: NexusServerConfig) -> Iterator[tuple[TestClient, NexusServerContext]]:
    host, port = node2_config.rqlite_host.split(":")
    db = create_connection(host, int(port), node2_config.api_key)
    context = NexusServerContext(db=db, config=node2_config)
    app = create_app(ctx=context)

    with TestClient(app) as client:
        time.sleep(0.1)
        yield client, context


@pytest.fixture
def job_payload() -> dict:
    return {
        "command": "echo 'Multi-node test'",
        "git_repo_url": "https://github.com/elyxlz/nexus.git",
        "git_branch": "master",
        "user": "multinode_test_user",
        "discord_id": None,
        "num_gpus": 1,
        "env": {},
        "jobrc": None,
        "priority": 0,
        "search_wandb": False,
        "notifications": [],
        "gpu_idxs": None,
        "ignore_blacklist": False,
    }


async def run_scheduler_iteration(context):
    """Run a single iteration of the scheduler loop."""
    await scheduler.update_running_jobs(ctx=context)
    await scheduler.update_wandb_urls(ctx=context)
    await scheduler.start_queued_jobs(ctx=context)


def test_job_node_assignment(node1_client, node2_client, job_payload, artifact_data):
    """Test that jobs can be assigned to specific nodes."""
    client1, ctx1 = node1_client
    client2, ctx2 = node2_client

    # Upload artifact to node 1
    artifact_id = upload_test_artifact(client1, artifact_data)

    # Create job payload with artifact ID
    payload = {**job_payload, "artifact_id": artifact_id}

    # 1. Submit job without specific node assignment
    response1 = client1.post("/v1/jobs", json=payload)
    assert response1.status_code == 201
    job1_id = response1.json()["id"]

    # Verify no node is assigned initially
    job1 = client1.get(f"/v1/jobs/{job1_id}").json()
    assert job1["node"] is None

    # Run scheduler on node 1
    import asyncio

    asyncio.get_event_loop().run_until_complete(run_scheduler_iteration(ctx1))

    # Verify node 1 claimed the job
    job1_updated = client1.get(f"/v1/jobs/{job1_id}").json()
    assert job1_updated["node"] == "node1"

    # 2. Submit job with explicit node assignment
    payload2 = {**payload, "node": "node2"}
    response2 = client1.post("/v1/jobs", json=payload2)
    assert response2.status_code == 201
    job2_id = response2.json()["id"]

    # Verify node2 is pre-assigned
    job2 = client1.get(f"/v1/jobs/{job2_id}").json()
    assert job2["node"] == "node2"

    # Run scheduler on node 1 (should not claim node2's job)
    asyncio.get_event_loop().run_until_complete(run_scheduler_iteration(ctx1))

    # Verify node assignment didn't change
    job2_updated = client1.get(f"/v1/jobs/{job2_id}").json()
    assert job2_updated["node"] == "node2"
    assert job2_updated["status"] == "queued"

    # Run scheduler on node 2 (should claim its job)
    asyncio.get_event_loop().run_until_complete(run_scheduler_iteration(ctx2))

    # Verify node2 is running its assigned job
    job2_node2 = client2.get(f"/v1/jobs/{job2_id}").json()
    assert job2_node2["node"] == "node2"

    # 3. Test that node1 can still see node2's jobs
    jobs_node1 = client1.get("/v1/jobs").json()
    job_ids = [j["id"] for j in jobs_node1]
    assert job1_id in job_ids
    assert job2_id in job_ids
