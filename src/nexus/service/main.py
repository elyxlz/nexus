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
        ctx.logger.info("scheduler starting")
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

    service_dir = pl.Path.home() / ".nexus_service"
    _config = config.NexusServiceConfig(service_dir=service_dir)
    _env = env.NexusServiceEnv()

    db_path = ":memory:"
    log_dir = None
    if _config.service_dir is not None:
        db_path = str(config.get_db_path(_config.service_dir))
        log_dir = config.get_log_dir(_config.service_dir)
        setup.create_persistent_directory(_config, _env=_env)

    _logger = logger.create_service_logger(log_dir, name="nexus_service", log_level=_config.log_level)
    _db = db.create_connection(_logger, db_path=db_path)

    ctx = context.NexusServiceContext(db=_db, config=_config, env=_env, logger=_logger)

    app = create_app(ctx)
    uvicorn.run(app, host=_config.host, port=_config.port, log_level=_config.log_level)
