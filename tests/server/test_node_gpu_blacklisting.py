import time

import pytest
from fastapi.testclient import TestClient

from nexus.server.api.app import create_app
from nexus.server.core.config import NexusServerConfig
from nexus.server.core.context import NexusServerContext
from nexus.server.core.db import create_connection


@pytest.fixture
def server_config() -> NexusServerConfig:
    """Create server configuration for testing."""
    return NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54326,
        node_name="test_node_1",
        mock_gpus=True,
        api_key="test_api_key",
    )


@pytest.fixture
def alt_server_config() -> NexusServerConfig:
    """Create alternative server configuration for testing."""
    return NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54327,
        node_name="test_node_2",
        mock_gpus=True,
        api_key="test_api_key",
    )


@pytest.fixture
def create_app_client(server_config: NexusServerConfig):
    """Create a test client with the server config."""
    host, port = server_config.rqlite_host.split(":")
    db = create_connection(host, int(port), server_config.api_key)
    context = NexusServerContext(db=db, config=server_config)
    app = create_app(ctx=context)

    with TestClient(app) as client:
        time.sleep(0.2)  # Allow server to initialize
        yield client, context


def test_node_gpu_blacklisting(create_app_client, alt_server_config):
    """Test that GPU blacklisting works correctly in multi-node setup."""
    client, context = create_app_client

    # Blacklist a GPU on the first node
    blacklist_resp = client.put("/v1/gpus/0/blacklist")
    assert blacklist_resp.status_code == 200

    # Blacklist a GPU on the second node (using a direct db call since we don't have an API client for it)
    from nexus.server.core import db

    db.add_blacklisted_gpu(context.db, gpu_idx=1, node=alt_server_config.node_name)

    # Get GPUs - should only show blacklisted GPUs for the current node
    gpus_resp = client.get("/v1/gpus")
    assert gpus_resp.status_code == 200
    gpus = gpus_resp.json()

    # Find GPU with index 0 (should be blacklisted)
    gpu_0 = next((g for g in gpus if g["index"] == 0), None)
    assert gpu_0 is not None
    assert gpu_0["is_blacklisted"] is True

    # Find GPU with index 1 (should not be blacklisted in current node's view)
    gpu_1 = next((g for g in gpus if g["index"] == 1), None)
    assert gpu_1 is not None
    assert gpu_1["is_blacklisted"] is False

    # Verify both nodes have blacklisted GPU records in the db
    blacklisted = db.list_blacklisted_gpus(context.db)  # All blacklisted GPUs across nodes
    assert 0 in blacklisted  # GPU 0 blacklisted on test_node_1
    assert 1 in blacklisted  # GPU 1 blacklisted on test_node_2

    # Node-specific blacklisted GPUs
    node1_blacklisted = db.list_blacklisted_gpus(context.db, node=server_config.node_name)
    node2_blacklisted = db.list_blacklisted_gpus(context.db, node=alt_server_config.node_name)

    assert node1_blacklisted == [0]
    assert node2_blacklisted == [1]
