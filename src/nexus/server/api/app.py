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

    @app.exception_handler(exc.NexusServerError)
    async def nexus_exception_handler(request: fa.Request, error: exc.NexusServerError):
        status_code = getattr(error, "STATUS_CODE", 500)
        ctx.logger.error(f"API error: {error.code} - {error.message}")
        return JSONResponse(
            status_code=status_code,
            content={
                "error": error.code,
                "message": error.message,
                "status_code": status_code,
            },
        )

    @app.exception_handler(exc.NotFoundError)
    async def not_found_exception_handler(request: fa.Request, error: exc.NotFoundError):
        ctx.logger.warning(f"Not found error: {error.code} - {error.message}")
        return JSONResponse(
            status_code=404,
            content={"error": error.code, "message": error.message, "status_code": 404},
        )

    @app.exception_handler(exc.InvalidRequestError)
    async def invalid_request_exception_handler(request: fa.Request, error: exc.InvalidRequestError):
        ctx.logger.warning(f"Invalid request error: {error.code} - {error.message}")
        return JSONResponse(
            status_code=400,
            content={"error": error.code, "message": error.message, "status_code": 400},
        )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: fa.Request, error: ValidationError):
        errors = error.errors()
        error_details = ", ".join([f"{e['loc'][-1]}: {e['msg']}" for e in errors])
        ctx.logger.warning(f"Validation error: {error_details}")
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": error_details,
                "status_code": 422,
                "detail": errors,
            },
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: fa.FastAPI):
        ctx.logger.info("Scheduler starting")
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
            ctx.logger.info("Nexus server stopped")

    app.router.lifespan_context = lifespan
    app.include_router(router.router)

    return app
