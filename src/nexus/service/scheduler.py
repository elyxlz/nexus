import asyncio

from nexus.service import models
from nexus.service.config import NexusServiceConfig
from nexus.service.git import cleanup_repo
from nexus.service.gpu import get_available_gpus
from nexus.service.job import start_job, update_job_status_if_completed
from nexus.service.logger import logger
from nexus.service.state import (
    clean_old_completed_jobs_in_state,
    save_state,
    update_jobs_in_state,
)


async def update_running_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Update status of running jobs and handle completed ones."""
    jobs_to_update = []

    for job in [j for j in state.jobs if j.status == "running"]:
        updated_job = update_job_status_if_completed(job, config.log_dir)
        if updated_job.status != "running":
            if updated_job.status == "completed":
                logger.info(f"Job {job.id} completed successfully")
            else:
                logger.error(f"Job {job.id} failed: {updated_job.error_message}")

            cleanup_repo(job_repo_dir=config.repo_dir / updated_job.id)
            jobs_to_update.append(updated_job)

    if jobs_to_update:
        update_jobs_in_state(state, jobs=jobs_to_update)
        save_state(state, state_path=config.state_path)
        logger.info(f"Updated status for {len(jobs_to_update)} completed jobs")


async def clean_old_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Remove old completed jobs based on history limit."""
    initial_count = len(state.jobs)
    clean_old_completed_jobs_in_state(state, max_completed=config.history_limit)

    if len(state.jobs) < initial_count:
        save_state(state, state_path=config.state_path)
        logger.debug(f"Cleaned {initial_count - len(state.jobs)} old completed jobs")


async def start_queued_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Start queued jobs on available GPUs."""
    available_gpus = get_available_gpus(state)
    queued_jobs = [j for j in state.jobs if j.status == "queued"]

    if not queued_jobs:
        logger.debug("No jobs in queue")
        return

    if not available_gpus:
        running_count = len([j for j in state.jobs if j.status == "running"])
        logger.debug(f"No available GPUs. Currently running {running_count} jobs")
        return

    started_jobs = []
    for gpu in available_gpus:
        if not queued_jobs:
            break

        job = queued_jobs.pop(0)
        started_job = start_job(job, gpu_index=gpu.index, log_dir=config.log_dir, repo_dir=config.repo_dir, env_file=config.env_file)

        started_jobs.append(started_job)
        if started_job.status == "running":
            logger.info(f"Started job {started_job.id} with command '{started_job.command}' on GPU {gpu.index}")

    if started_jobs:
        update_jobs_in_state(state, jobs=started_jobs)
        save_state(state, state_path=config.state_path)
        logger.info(f"Started {len(started_jobs)} new jobs")


async def process_scheduler_tick(state: models.ServiceState, config: NexusServiceConfig):
    """Process a single scheduler iteration."""
    await update_running_jobs(state, config)
    await clean_old_jobs(state, config)
    await start_queued_jobs(state, config)


async def job_scheduler(state: models.ServiceState, config: NexusServiceConfig):
    """Main scheduler loop that processes jobs and manages GPU allocation."""
    while True:
        if not state.is_paused:
            try:
                await process_scheduler_tick(state, config)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
        else:
            logger.info("Scheduler is paused")
        await asyncio.sleep(config.refresh_rate)
