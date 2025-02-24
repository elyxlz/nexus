import dataclasses
import pathlib

import pytest

from nexus.service.core.config import NexusServiceConfig, load_config, save_config
from nexus.service.core.env import NexusServiceEnv, load_env, save_env
from nexus.service.core.logger import create_service_logger
from nexus.service.core.models import NexusServiceState
from nexus.service.state import load_state, save_state


@pytest.fixture
def real_logger(tmp_path: pathlib.Path):
    log_dir = tmp_path / "logs"
    return create_service_logger(log_dir, name="test_logger")


@pytest.fixture
def dummy_state() -> NexusServiceState:
    # Create a dummy state with no jobs and no blacklisted GPUs.
    return NexusServiceState(status="running", jobs=(), blacklisted_gpus=())


def test_save_and_load_state(tmp_path: pathlib.Path, dummy_state: NexusServiceState, real_logger):
    state_file = tmp_path / "state.json"
    # Save the dummy state to a temporary file.
    save_state(dummy_state, state_file)
    # Load the state back from disk using the new load_state function.
    loaded_state = load_state(real_logger, state_file)
    # Compare the two states using dataclasses.asdict to check for equality.
    assert dataclasses.asdict(dummy_state) == dataclasses.asdict(loaded_state)


def test_save_and_load_config(tmp_path: pathlib.Path):
    # Create a dummy config with custom values and set the service_dir to a temporary path.
    dummy_config = NexusServiceConfig(
        service_dir=tmp_path / "nexus_service",
        refresh_rate=10,
        host="127.0.0.1",
        port=8000,
        webhooks_enabled=True,
        node_name="test_node",
        log_level="debug",
        mock_gpus=False,
    )
    assert dummy_config.service_dir is not None
    # Ensure the service directory exists.
    dummy_config.service_dir.mkdir(parents=True, exist_ok=True)
    # Save the configuration.
    save_config(dummy_config)
    # Load the configuration using the new load_config function.
    loaded_config = load_config(dummy_config.service_dir)
    # Compare key fields.
    assert loaded_config.refresh_rate == dummy_config.refresh_rate
    assert loaded_config.host == dummy_config.host
    assert loaded_config.port == dummy_config.port
    assert loaded_config.webhooks_enabled == dummy_config.webhooks_enabled
    assert loaded_config.node_name == dummy_config.node_name
    assert loaded_config.log_level == dummy_config.log_level
    assert loaded_config.mock_gpus == dummy_config.mock_gpus


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
    # Load the environment settings using the new load_env function.
    loaded_env = load_env(env_file)
    # Compare the loaded environment with the dummy environment.
    assert loaded_env.github_token == dummy_env.github_token
    assert loaded_env.discord_webhook_url == dummy_env.discord_webhook_url
    assert loaded_env.wandb_api_key == dummy_env.wandb_api_key
    assert loaded_env.wandb_entity == dummy_env.wandb_entity
