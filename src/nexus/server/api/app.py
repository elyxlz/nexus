import asyncio
import contextlib
import importlib.metadata

import fastapi as fa
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from nexus.server.api import router, scheduler
from nexus.server.core import context
from nexus.server.core import exceptions as exc
from nexus.server.utils import logger


def create_app(ctx: context.NexusServerContext) -> fa.FastAPI:
    app = fa.FastAPI(
        title="Nexus GPU Job Server",
        description="GPU Job Management Server",
        version=importlib.metadata.version("nexusai"),
    )
    app.state.ctx = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _register_handler(app: fa.FastAPI, exc_cls: type, status: int, *, level: str = "warning"):
        @app.exception_handler(exc_cls)
        async def _h(_, err):
            if isinstance(err, ValidationError):
                detail = err.errors()
                code, msg, sc = "VALIDATION_ERROR", ", ".join(f"{e['loc'][-1]}: {e['msg']}" for e in detail), status
                body = {"detail": detail}
            else:
                sc = getattr(err, "STATUS_CODE", status)
                code, msg = getattr(err, "code", exc_cls.__name__), getattr(err, "message", str(err))
                body = {}

            getattr(logger, level)(f"{code} – {msg}")
            return JSONResponse(status_code=sc, content={"error": code, "message": msg, "status_code": sc, **body})

    _register_handler(app, exc.NexusServerError, 500, level="error")
    _register_handler(app, exc.NotFoundError, 404, level="warning")
    _register_handler(app, exc.InvalidRequestError, 400, level="warning")
    _register_handler(app, ValidationError, 422, level="warning")

    @contextlib.asynccontextmanager
    async def lifespan(app: fa.FastAPI):
        logger.info("Scheduler starting")
        coro = scheduler.scheduler_loop(ctx=app.state.ctx)
        scheduler_task = asyncio.create_task(coro)
        try:
            yield
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            ctx.db.close()
            logger.info("Nexus server stopped")

    app.router.lifespan_context = lifespan
    app.include_router(router.router)

    return app
