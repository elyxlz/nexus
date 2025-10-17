import asyncio
import contextlib
import importlib.metadata
import secrets

import fastapi as fa
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from nexus.server.api import router, scheduler
from nexus.server.core import context
from nexus.server.core import exceptions as exc
from nexus.server.utils import logger


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in ["/health", "/v1/health"]:
            return await call_next(request)

        client_host = request.client.host if request.client else None
        if client_host in ["127.0.0.1", "::1", "localhost"]:
            return await call_next(request)

        ctx = request.app.state.ctx
        if not ctx.config.api_token:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Missing token"})

        if not secrets.compare_digest(auth[7:], ctx.config.api_token):
            return JSONResponse(status_code=401, content={"error": "Invalid token"})

        return await call_next(request)


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

    app.add_middleware(AuthMiddleware)

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
