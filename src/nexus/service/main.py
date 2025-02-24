import asyncio
import contextlib
import importlib.metadata
import pathlib as pl

import fastapi as fa
import uvicorn

from nexus.service.api import router, scheduler
from nexus.service.core import config, context, db, env, logger
from nexus.service.installation import setup


def create_app(ctx: context.NexusServiceContext) -> fa.FastAPI:
    app = fa.FastAPI(
        title="Nexus GPU Job Service",
        description="GPU Job Management Service",
        version=importlib.metadata.version("nexusai"),
    )
    app.state.ctx = ctx

    @contextlib.asynccontextmanager
    async def lifespan(app: fa.FastAPI):
        scheduler_task = asyncio.create_task(scheduler.scheduler_loop(ctx=app.state.ctx))
        try:
            yield
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

            ctx.db.close()
            ctx.logger.info("Nexus service stopped")

    app.router.lifespan_context = lifespan
    app.include_router(router.router)

    return app


def main():
    current_version = importlib.metadata.version("nexusai")
    remote_version = setup.fetch_latest_version()
    if remote_version != current_version:
        print(f"new version available: {remote_version}, please upgrade nexus")

    setup.verify_external_dependencies()

    service_dir = pl.Path.home() / ".nexus_service"  # TODO: for now

    # TODO: add a check if these were already persisted and skip
    # TODO: interactively go through these
    _config = config.NexusServiceConfig(service_dir=service_dir)
    _env = env.NexusServiceEnv()

    db_path = ":memory:"
    log_dir = None
    if _config.service_dir is not None:
        db_path = str(config.get_db_path(_config.service_dir))
        log_dir = config.get_log_dir(_config.service_dir)
        setup.create_persistent_directory(_config, _env=_env)

    _db = db.create_connection(db_path)
    _logger = logger.create_service_logger(log_dir, name="nexus_service", log_level=_config.log_level)

    ctx = context.NexusServiceContext(db=_db, config=_config, env=_env, logger=_logger)

    app = create_app(ctx)
    uvicorn.run(app, host=_config.host, port=_config.port, log_level=_config.log_level)

    # # If the user ran "nexus-service uninstall", then run uninstall and exit.
    # if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
    #     setup.uninstall()
    #     sys.exit(0)
    #
    # # If the installed version does not match the current version, uninstall first.
    # if not setup.is_installed_current_version(current_version):
    #     if setup.already_installed():
    #         print("New version detected. Reinstalling...")
    #         setup.uninstall()
    #     setup.install()
    #
    # _env = config.NexusServiceEnv()
    #
    # # If persistence is enabled, create required files and directories.
    # if _config.persist_to_disk:
    #     config.create_required_files_and_dirs(_config, env=_env)
    #
    # # Load state from disk if persistence is enabled and a state file exists;
    # # otherwise, create a default in-memory state.
    # if _config.persist_to_disk and config.get_state_path(_config.service_dir).exists():
    #     state.load_state(config.get_state_path(_config.service_dir))
    # else:
    #     state.create_default_state()
    #
