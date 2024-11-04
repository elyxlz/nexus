import asyncio

from nexus.service import models
from nexus.service.config import NexusServiceConfig
from nexus.service.format import format_job_action
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
        updated_job = update_job_status_if_completed(job, jobs_dir=config.jobs_dir)
        if updated_job.status != "running":
            if updated_job.status == "completed":
                logger.info(format_job_action(updated_job, action="completed"))
            else:
                logger.error(format_job_action(updated_job, action="failed"))
                log_file = config.jobs_dir / updated_job.id / "output.log"
                if log_file.exists():
                    with open(log_file, "r") as f:
                        last_lines = f.readlines()[-5:]
                    logger.error(f"Last 10 lines of job log:\n{''.join(last_lines)}")

            cleanup_repo(config.jobs_dir, job_id=updated_job.id)
            jobs_to_update.append(updated_job)

    if jobs_to_update:
        update_jobs_in_state(state, jobs=jobs_to_update)
        save_state(state, state_path=config.state_path)
        logger.debug(f"Updated status for {len(jobs_to_update)} completed jobs")


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
        started_job = start_job(job, gpu_index=gpu.index, jobs_dir=config.jobs_dir, env_file=config.env_file)

        started_jobs.append(started_job)
        if started_job.status == "running":
            logger.info(format_job_action(job, action="started"))

    if started_jobs:
        update_jobs_in_state(state, jobs=started_jobs)
        save_state(state, state_path=config.state_path)
        logger.info(f"Started {len(started_jobs)} new jobs. Remaining jobs in queue: {len(queued_jobs)}")


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
