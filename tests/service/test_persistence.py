import dataclasses
import pathlib

import pytest
import toml

from nexus.service.models import NexusServiceState
from nexus.service.state import save_state, load_state
from nexus.service.config import (
    NexusServiceConfig,
    NexusServiceEnv,
    get_config_path,
    get_env_path,
    save_config,
    save_env,
)

# --- State Persistence Tests ---


@pytest.fixture
def dummy_state() -> NexusServiceState:
    # Create a dummy state with no jobs and no blacklisted GPUs.
    return NexusServiceState(status="running", jobs=(), blacklisted_gpus=())


def test_save_and_load_state(tmp_path: pathlib.Path, dummy_state: NexusServiceState):
    state_file = tmp_path / "state.json"
    # Save the dummy state to a temporary file.
    save_state(dummy_state, state_file)
    # Load the state back from disk.
    loaded_state = load_state(state_file)
    # Compare the two states using dataclasses.asdict to check for equality.
    assert dataclasses.asdict(dummy_state) == dataclasses.asdict(loaded_state)


# --- Configuration Persistence Tests ---


def test_save_and_load_config(tmp_path: pathlib.Path):
    # Create a dummy config with custom values and set the service_dir to a temporary path.
    dummy_config = NexusServiceConfig(
        service_dir=tmp_path / "nexus_service",
        refresh_rate=10,
        history_limit=500,
        host="127.0.0.1",
        port=8000,
        webhooks_enabled=True,
        node_name="test_node",
        log_level="debug",
        mock_gpus=False,
        persist_to_disk=True,
    )
    # Ensure the service directory exists.
    dummy_config.service_dir.mkdir(parents=True, exist_ok=True)
    config_file = get_config_path(dummy_config.service_dir)
    # Save the configuration.
    save_config(dummy_config)
    # Load the file using toml and check key fields.
    loaded_toml = toml.load(config_file)
    assert loaded_toml["refresh_rate"] == dummy_config.refresh_rate
    assert loaded_toml["host"] == dummy_config.host
    assert loaded_toml["port"] == dummy_config.port


# --- Environment File Persistence Tests ---


def test_save_and_load_env(tmp_path: pathlib.Path):
    # Create a dummy environment configuration.
    dummy_env = NexusServiceEnv(
        github_token="dummy_token",
        discord_webhook_url="https://dummy.webhook",
        wandb_api_key="dummy_key",
        wandb_entity="dummy_entity",
    )
    env_file = tmp_path / "dummy.env"
    # Save the environment settings to a temporary file.
    save_env(dummy_env, env_file)
    # Read the file content.
    content = env_file.read_text()
    # Verify that the expected key-value pairs are present.
    assert "GITHUB_TOKEN=dummy_token" in content
    assert "DISCORD_WEBHOOK_URL=https://dummy.webhook" in content
    assert "WANDB_API_KEY=dummy_key" in content
    assert "WANDB_ENTITY=dummy_entity" in content
