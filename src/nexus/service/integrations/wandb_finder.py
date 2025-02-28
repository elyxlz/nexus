import asyncio
import pathlib as pl

import wandb
import wandb.errors

from nexus.service.core import exceptions as exc
from nexus.service.core import logger, schemas

__all__ = ["find_wandb_run_by_nexus_id"]


@exc.handle_exception(wandb.errors.CommError, exc.WandBError, message="W&B API communication error")
async def check_project_for_run(_logger: logger.NexusServiceLogger, project, run_id: str, api) -> str:
    _logger.debug(f"Checking project {project.name} for run {run_id}")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: api.run(f"{project.entity}/{project.name}/{run_id}"))
    url = f"https://wandb.ai/{project.entity}/{project.name}/runs/{run_id}"
    _logger.debug(f"Found run URL: {url}")
    return url


@exc.handle_exception(OSError, exc.WandBError, message="Error reading W&B metadata files", reraise=False)
async def find_run_id_from_metadata(
    _logger: logger.NexusServiceLogger, dirs: list[str], nexus_job_id: str
) -> str | None:
    _logger.debug(f"Searching for nexus job ID {nexus_job_id} in directories: {dirs}")
    loop = asyncio.get_running_loop()

    for root_dir in dirs:
        root_path = pl.Path(root_dir)
        _logger.debug(f"Scanning directory: {root_path}")
        metadata_files = await loop.run_in_executor(None, lambda: list(root_path.rglob("wandb-metadata.json")))

        for metadata_file in metadata_files:
            _logger.debug(f"Checking metadata file: {metadata_file}")
            content = await loop.run_in_executor(None, lambda: metadata_file.read_text())

            if nexus_job_id in content:
                run_id = str(metadata_file.parent.parent).split("-")[-1]
                _logger.debug(f"Found matching run ID: {run_id}")
                return run_id

    _logger.debug(f"No matching run ID found in metadata files for job ID: {nexus_job_id}")
    return None


@exc.handle_exception(wandb.errors.Error, exc.WandBError, message="W&B API error", reraise=False)
async def find_wandb_run_by_nexus_id(
    _logger: logger.NexusServiceLogger, job: schemas.Job, api_timeout: int = 2
) -> str | None:
    nexus_job_id = job.id
    dirs = [str(job.dir)] if job.dir else []
    _logger.debug(f"Starting search for nexus job ID: {nexus_job_id}")

    wandb_api_key = job.env.get("WANDB_API_KEY")
    wandb_entity = job.env.get("WANDB_ENTITY")

    if not wandb_api_key:
        raise exc.WandBError("Missing WANDB_API_KEY in job environment")

    if not wandb_entity:
        raise exc.WandBError("Missing WANDB_ENTITY in job environment")

    run_id = await find_run_id_from_metadata(_logger, dirs, nexus_job_id)
    if run_id is None:
        return None

    loop = asyncio.get_running_loop()

    if wandb_api_key:
        api = await loop.run_in_executor(None, lambda: wandb.Api(api_key=wandb_api_key, timeout=api_timeout))
    else:
        api = await loop.run_in_executor(None, lambda: wandb.Api(timeout=api_timeout))

    entity = wandb_entity or api.default_entity
    if not entity:
        _logger.debug("No W&B entity provided and no default entity found")
        return None

    _logger.debug(f"Fetching projects for entity: {entity}")
    projects = await loop.run_in_executor(None, lambda: api.projects(entity))

    _logger.debug("Starting parallel project search")
    tasks = [check_project_for_run(_logger, project, run_id, api) for project in projects]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            continue
        if result and isinstance(result, str):
            _logger.debug(f"Found matching W&B URL: {result}")
            return result

    _logger.debug(f"W&B run found in metadata but not in any projects: {run_id}")
    return None
