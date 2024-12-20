import asyncio
import datetime as dt

from nexus.service import models
from nexus.service.config import NexusServiceConfig
from nexus.service.format import format_job_action
from nexus.service.git import cleanup_git_tag
from nexus.service.gpu import get_gpus
from nexus.service.job import end_job, get_job_logs, is_job_session_running, kill_job_session, start_job
from nexus.service.logger import logger
from nexus.service.state import (
    clean_old_completed_jobs_in_state,
    save_state,
    update_jobs_in_state,
)
from nexus.service.wandb_finder import find_wandb_run_by_nexus_id
from nexus.service.webhooks import notify_job_completed, notify_job_failed, notify_job_started, update_job_wandb


async def update_running_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Update status of running jobs and handle completed ones."""
    jobs_to_update = []

    for job in [j for j in state.jobs if j.status == "running"]:
        if job.marked_for_kill and is_job_session_running(job.id):
            kill_job_session(job.id)
            updated_job = end_job(job, jobs_dir=config.jobs_dir, killed=True)

        elif not is_job_session_running(job.id):
            updated_job = end_job(job, jobs_dir=config.jobs_dir, killed=False)
        else:
            continue

        if updated_job.status != "running":
            action = "completed" if updated_job.status == "completed" else "failed"
            log_func = logger.info if action == "completed" else logger.error
            log_func(format_job_action(updated_job, action=action))

            # Add git tag cleanup here
            running_jobs = [j for j in state.jobs if j.status == "running"]
            cleanup_git_tag(updated_job, running_jobs=running_jobs)

            if config.webhooks_enabled:
                notify_func = notify_job_completed if action == "completed" else notify_job_failed
                await notify_func(updated_job, jobs_dir=config.jobs_dir)

            if action == "failed":
                last_lines = get_job_logs(updated_job.id, jobs_dir=config.jobs_dir, last_n_lines=10)
                if last_lines is not None:
                    logger.error(f"Last 10 lines of job log:\n{''.join(last_lines)}")

        jobs_to_update.append(updated_job)

    if jobs_to_update:
        update_jobs_in_state(state, jobs=jobs_to_update)
        save_state(state, state_path=config.state_path)
        logger.debug(f"Updated status for {len(jobs_to_update)} completed jobs")


async def update_wandb_urls(state: models.ServiceState, config: NexusServiceConfig) -> None:
    """Update W&B URLs for running jobs that don't have them yet."""
    jobs_to_update = []
    search_dirs = []
    current_time = dt.datetime.now().timestamp()

    for job in [j for j in state.jobs if j.status == "running" and not j.wandb_url]:
        assert job.started_at is not None
        job_runtime = current_time - job.started_at

        # Skip if runtime is more than 5 minutes
        if job_runtime > 360:
            continue

        job_repo_dir = config.jobs_dir / job.id / "repo"
        if job_repo_dir.exists():
            search_dirs.append(str(job_repo_dir))

        wandb_url = find_wandb_run_by_nexus_id(search_dirs, nexus_job_id=job.id)
        if wandb_url:
            job.wandb_url = wandb_url
            jobs_to_update.append(job)
            logger.info(f"Associated job {job.id} with W&B run: {wandb_url}")

            # Update the webhook message with the W&B URL
            if config.webhooks_enabled:
                await update_job_wandb(job)

    if jobs_to_update:
        update_jobs_in_state(state, jobs=jobs_to_update)
        save_state(state, state_path=config.state_path)


async def clean_old_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Remove old completed jobs based on history limit."""
    initial_count = len(state.jobs)
    state = clean_old_completed_jobs_in_state(state, max_completed=config.history_limit)

    if len(state.jobs) < initial_count:
        save_state(state, state_path=config.state_path)
        logger.debug(f"Cleaned {initial_count - len(state.jobs)} old completed jobs")


async def start_queued_jobs(state: models.ServiceState, config: NexusServiceConfig):
    """Start queued jobs on available GPUs."""
    available_gpus = [g for g in get_gpus(state) if g.is_available]
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
        logger.info(format_job_action(job, action="started"))

        if config.webhooks_enabled:
            await notify_job_started(job)

    if started_jobs:
        update_jobs_in_state(state, jobs=started_jobs)
        save_state(state, state_path=config.state_path)
        logger.info(f"Started {len(started_jobs)} new jobs. Remaining jobs in queue: {len(queued_jobs)}")


async def process_scheduler_tick(state: models.ServiceState, config: NexusServiceConfig):
    """Process a single scheduler iteration."""
    await update_running_jobs(state, config)
    await update_wandb_urls(state, config)
    await clean_old_jobs(state, config)
    await start_queued_jobs(state, config)


async def job_scheduler(state: models.ServiceState, config: NexusServiceConfig):
    """Main scheduler loop that processes jobs and manages GPU allocation."""
    while True:
        try:
            await process_scheduler_tick(state, config)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(config.refresh_rate)
