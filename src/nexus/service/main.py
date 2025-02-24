import asyncio
import contextlib
import importlib.metadata

import fastapi as fa

from nexus.service.api import router, scheduler
from nexus.service.core import context


def create_app(ctx: context.NexusServiceContext) -> fa.FastAPI:
    app = fa.FastAPI(
        title="Nexus GPU Job Service",
        description="GPU Job Management Service",
        version=importlib.metadata.version("nexusai"),
    )
    app.state.ctx = ctx

    @contextlib.asynccontextmanager
    async def lifespan(app: fa.FastAPI):
        scheduler_task = asyncio.create_task(scheduler.scheduler_loop(ctx=app.state.context))
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


#
# def main():
#     current_version = importlib.metadata.version("nexusai")
#     remote_version = setup.fetch_latest_version()
#     if remote_version != current_version:
#         print(f"new version available: {remote_version}, please upgrade nexus")
#
#     setup.verify_external_dependencies()
#
#     # If the user ran "nexus-service uninstall", then run uninstall and exit.
#     if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
#         setup.uninstall()
#         sys.exit(0)
#
#     # If the installed version does not match the current version, uninstall first.
#     if not setup.is_installed_current_version(current_version):
#         if setup.already_installed():
#             print("New version detected. Reinstalling...")
#             setup.uninstall()
#         setup.install()
#
#     _config = config.NexusServiceConfig()
#     _env = config.NexusServiceEnv()
#
#     # If persistence is enabled, create required files and directories.
#     if _config.persist_to_disk:
#         config.create_required_files_and_dirs(_config, env=_env)
#
#     # Load state from disk if persistence is enabled and a state file exists;
#     # otherwise, create a default in-memory state.
#     if _config.persist_to_disk and config.get_state_path(_config.service_dir).exists():
#         state.load_state(config.get_state_path(_config.service_dir))
#     else:
#         state.create_default_state()
#
#     app = create_app()
#     uvicorn.run(app, host=app.state.config.host, port=app.state.config.port, log_level=app.state.config.log_level)
