import pytest
import tempfile
import pathlib as pl
import time
from nexus.server.core import config, rqlite

# Set a fixed API key for all test runs to ensure consistent authentication
TEST_API_KEY = "test_api_key"


def setup_test_db(port=4001):
    """Set up a clean database instance for testing.

    Args:
        port: Port number to use for the database

    Returns:
        str: The database host:port string to use in test configurations
    """
    # Create temporary files for this instance
    data_dir = pl.Path(tempfile.mkdtemp(prefix=f"rqlite_data_"))
    auth_file = pl.Path(tempfile.mktemp(prefix="rqlite_auth_"))

    # Set up the rqlite host
    rqlite_host = f"localhost:{port}"

    # Create a configuration for rqlite
    test_config = config.NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54321,
        node_name="test_node",
        mock_gpus=True,
        api_key=TEST_API_KEY,
        rqlite_host=rqlite_host,
    )

    # Write the authentication file with our test API key
    rqlite.write_auth_config(auth_file, TEST_API_KEY)

    # Start up rqlite for this test
    rqlite.setup_rqlite(
        test_config,
        auth_file_path=auth_file,
        data_dir_path=data_dir
    )

    # Wait a moment to ensure rqlite is fully initialized
    time.sleep(2)

    # Return the rqlite host to connect to
    return rqlite_host


@pytest.fixture(scope="session")
def rqlite_server():
    """Fixture to start a standard rqlite server for testing."""
    # Create a standard rqlite server on the default port
    return setup_test_db(port=4001)


@pytest.fixture(scope="function")
def server_config(rqlite_server):
    """Create a standard server configuration for testing."""
    # Create a standard configuration
    return config.NexusServerConfig(
        server_dir=None,
        refresh_rate=1,
        port=54321,
        node_name="test_node",
        mock_gpus=True,
        api_key=TEST_API_KEY,
        rqlite_host=rqlite_server,
    )