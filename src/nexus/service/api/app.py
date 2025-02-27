import asyncio
import contextlib
import importlib.metadata

import fastapi as fa

from nexus.service.api import router, scheduler
from nexus.service.core import context


def create_app(ctx: context.NexusServiceContext) -> fa.FastAPI:
    """Create and configure the FastAPI application."""
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
