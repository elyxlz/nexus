import asyncio
import shutil
import contextlib
import importlib.metadata
import uvicorn
from fastapi import FastAPI

from nexus.service import state, scheduler, logger, models, router, config


def check_for_new_version(current_version: str) -> None:
    try:
        import requests

        r = requests.get("https://pypi.org/pypi/nexusai/json", timeout=2)
        r.raise_for_status()
        data = r.json()
        latest = data["info"]["version"]
        if latest != current_version:
            logger.logger.warning(
                f"A newer version of nexusai ({latest}) is available on PyPI. Current: {current_version}"
            )
    except Exception as e:
        logger.logger.debug(f"Failed to check for new version: {e}")


def check_external_dependencies() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("Critical dependency missing: Git is not installed or not in PATH.")
    if shutil.which("screen") is None:
        raise RuntimeError("Critical dependency missing: screen is not installed or not in PATH.")


def create_app(
    custom_state: models.NexusServiceState | None = None,
    custom_config: config.NexusServiceConfig | None = None,
    custom_env: config.NexusServiceEnv | None = None,
) -> FastAPI:
    _config = custom_config or config.NexusServiceConfig()
    _env = custom_env or config.NexusServiceEnv()

    # If persistence is enabled, create required files and directories.
    if _config.persist_to_disk:
        config.create_required_files_and_dirs(_config, env=_env)

    # Load state from disk if persistence is enabled and a state file exists;
    # otherwise, create a default in-memory state.
    if custom_state is None and _config.persist_to_disk and config.get_state_path(_config.service_dir).exists():
        custom_state = state.load_state(config.get_state_path(_config.service_dir))
    else:
        custom_state = custom_state or state.create_default_state()

    # Create the FastAPI application.
    app = FastAPI(
        title="Nexus GPU Job Service",
        description="GPU Job Management Service",
        version=importlib.metadata.version("nexusai"),
    )
    app.state.config = _config
    app.state.state = custom_state

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start the scheduler loop.
        scheduler_task = asyncio.create_task(scheduler.scheduler_loop(_state=app.state.state, _config=app.state.config))
        try:
            yield
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            # Save state to disk if persistence is enabled.
            if app.state.config.persist_to_disk:
                state.save_state(app.state.state, state_path=app.state.config.state_path)
            logger.logger.info("Nexus service stopped")

    app.router.lifespan_context = lifespan

    # Include the API routes.
    app.include_router(router.router)

    return app


def main():
    current_version = importlib.metadata.version("nexusai")
    check_for_new_version(current_version)
    check_external_dependencies()
    app = create_app()
    uvicorn.run(app, host=app.state.config.host, port=app.state.config.port, log_level=app.state.config.log_level)
