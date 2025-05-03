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

    def _register_handler(app: fa.FastAPI, exc_cls: type, default_status: int = 500, log_level: str = "error"):
        @app.exception_handler(exc_cls)
        async def handler(request: fa.Request, error: Exception):
            if isinstance(error, ValidationError):
                errors = error.errors()
                error_details = ", ".join([f"{e['loc'][-1]}: {e['msg']}" for e in errors])
                message = error_details
                code = "VALIDATION_ERROR"
                status_code = default_status
                extra = {"detail": errors}
            else:
                status_code = getattr(error, "STATUS_CODE", default_status)
                code = getattr(error, "code", exc_cls.__name__)
                message = getattr(error, "message", str(error))
                extra = {}

            if log_level == "error":
                logger.error(f"API error: {code} - {message}")
            else:
                logger.warning(f"{exc_cls.__name__}: {code} - {message}")
                
            content = {
                "error": code,
                "message": message,
                "status_code": status_code,
                **extra
            }
            
            return JSONResponse(status_code=status_code, content=content)
            
    _register_handler(app, exc.NexusServerError, 500, "error")
    _register_handler(app, exc.NotFoundError, 404, "warning")
    _register_handler(app, exc.InvalidRequestError, 400, "warning")
    _register_handler(app, ValidationError, 422, "warning")

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
