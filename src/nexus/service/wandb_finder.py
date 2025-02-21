import concurrent.futures
import os
import pathlib as pl

import wandb
import wandb.errors

from nexus.service import logger

__all__ = ["find_wandb_run_by_nexus_id"]


def check_project_for_run(project, run_id: str, api) -> str | None:
    logger.logger.debug(f"Checking project {project.name} for run {run_id}")
    try:
        api.run(f"{project.entity}/{project.name}/{run_id}")
        url = f"https://wandb.ai/{project.entity}/{project.name}/runs/{run_id}"
        logger.logger.debug(f"Found run URL: {url}")
        return url
    except wandb.errors.CommError:
        logger.logger.debug(f"Run {run_id} not found in project {project.name}")
        return None


def find_run_id_from_metadata(dirs: list[str], nexus_job_id: str) -> str | None:
    logger.logger.debug(f"Searching for nexus job ID {nexus_job_id} in directories: {dirs}")
    for root_dir in dirs:
        root_path = pl.Path(root_dir)
        logger.logger.debug(f"Scanning directory: {root_path}")
        for metadata_file in root_path.rglob("wandb-metadata.json"):
            logger.logger.debug(f"Checking metadata file: {metadata_file}")
            try:
                content = metadata_file.read_text()
                if nexus_job_id in content:
                    run_id = str(metadata_file.parent.parent).split("-")[-1]
                    logger.logger.debug(f"Found matching run ID: {run_id}")
                    return run_id
            except Exception as e:
                logger.logger.debug(f"Error reading metadata file {metadata_file}: {e}")
                continue
    logger.logger.debug("No matching run ID found in metadata files")
    return None


def find_wandb_run_by_nexus_id(dirs: list[str], nexus_job_id: str) -> str | None:
    logger.logger.debug(f"Starting search for nexus job ID: {nexus_job_id}")
    run_id = find_run_id_from_metadata(dirs, nexus_job_id)
    if not run_id:
        logger.logger.debug("No run ID found in metadata")
        return None

    logger.logger.debug("Initializing W&B API")
    api = wandb.Api(timeout=2)
    entity = os.getenv("WANDB_ENTITY") or api.default_entity
    if not entity:
        logger.logger.debug("No W&B entity found in environment or API default")
        return None

    try:
        logger.logger.debug(f"Fetching projects for entity: {entity}")
        projects = api.projects(entity)
    except Exception as e:
        logger.logger.debug(f"Error fetching projects: {e}")
        return None

    logger.logger.debug("Starting parallel project search")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_project_for_run, project, run_id, api): project for project in projects}

        for future in concurrent.futures.as_completed(futures):
            try:
                if result := future.result():
                    logger.logger.debug(f"Found matching W&B URL: {result}")
                    return result
            except Exception as e:
                project = futures[future]
                logger.logger.debug(f"Error checking project {project.name}: {e}")
                continue

    logger.logger.debug("No matching W&B URL found")
    return None
