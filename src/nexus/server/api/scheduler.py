import asyncio
import dataclasses as dc
import datetime as dt

from nexus.server.core import context, db, job, exceptions as exc
from nexus.server.external import gpu, notifications, wandb_finder, system
from nexus.server.utils import format, logger

__all__ = ["scheduler_loop"]


async def _for_running(ctx: context.NexusServerContext):
    for _job in db.list_jobs(ctx.db, status="running"):
        updated_job = _job

        if _job.marked_for_kill and job.is_job_running(job=_job):
            await job.kill_job(job=_job)
            updated_job = await job.async_end_job(_job=_job, killed=True)
            await job.async_cleanup_job_repo(job_dir=_job.dir)

        elif not job.is_job_running(job=_job):
            updated_job = await job.async_end_job(_job=_job, killed=False)
            await job.async_cleanup_job_repo(job_dir=_job.dir)

        else:
            continue

        if updated_job.status != "running":
            if updated_job.status == "completed":
                action = "completed"
            elif updated_job.status == "killed":
                action = "killed"
            else:
                action = "failed"

            logger.info(format.format_job_action(updated_job, action=action))

            if _job.notifications:
                await notifications.notify_job_action(_job=_job, action=action)

        db.update_job(conn=ctx.db, job=updated_job)


@db.safe_transaction
async def update_running_jobs(ctx: context.NexusServerContext) -> None:
    await _for_running(ctx)


async def _for_wandb_urls(ctx: context.NexusServerContext):
    for _job in db.list_jobs(ctx.db, status="running"):
        if _job.wandb_url or _job.started_at is None or "wandb" not in _job.integrations:
            continue

        if dt.datetime.now().timestamp() - _job.started_at > 720:
            continue

        wandb_url = await wandb_finder.find_wandb_run_by_nexus_id(job=_job)

        if wandb_url:
            updated = dc.replace(_job, wandb_url=wandb_url)
            db.update_job(conn=ctx.db, job=updated)
            logger.info(f"Associated job {_job.id} with W&B run: {wandb_url}")
            await notifications.update_notification_with_wandb(job=updated)


@db.safe_transaction
async def update_wandb_urls(ctx: context.NexusServerContext) -> None:
    await _for_wandb_urls(ctx)


async def _for_queued_jobs(ctx: context.NexusServerContext):
    queued_jobs = db.list_jobs(ctx.db, status="queued")
    ordered_jobs = job.get_queue(queued_jobs)
    if not ordered_jobs:
        logger.debug("No jobs in queue")
        return

    _job = ordered_jobs[0]

    running_jobs = db.list_jobs(conn=ctx.db, status="running")
    blacklisted_gpus = db.list_blacklisted_gpus(conn=ctx.db)
    all_gpus = gpu.get_gpus(
        running_jobs=running_jobs, blacklisted_gpus=blacklisted_gpus, mock_gpus=ctx.config.mock_gpus
    )
    available_gpus = [
        g for g in all_gpus if gpu.is_gpu_available(g, ignore_blacklist=_job.ignore_blacklist, required=_job.gpu_idxs)
    ]

    if not available_gpus:
        logger.debug("No available GPUs")
        return

    available_gpu_idxs = [g.index for g in available_gpus]

    if _job.gpu_idxs:
        if not all(idx in available_gpu_idxs for idx in _job.gpu_idxs):
            return
        job_gpu_idxs = _job.gpu_idxs
        logger.info(f"Using user-specified GPU indices {job_gpu_idxs} for job {_job.id}")
    elif _job.num_gpus <= len(available_gpu_idxs):
        job_gpu_idxs = available_gpu_idxs[: _job.num_gpus]
    else:
        return

    try:
        # Try to start the job
        started = await job.async_start_job(job=_job, gpu_idxs=job_gpu_idxs, server_dir=ctx.config.server_dir)

        db.update_job(conn=ctx.db, job=started)
        logger.info(format.format_job_action(started, action="started"))

        if started.notifications:
            job_with_notification = await notifications.notify_job_action(_job=started, action="started")
            db.update_job(conn=ctx.db, job=job_with_notification)

    except Exception as e:
        logger.error(f"Failed to start job {_job.id}: {str(e)}")

        failed_job = dc.replace(
            _job,
            status="failed",
            completed_at=dt.datetime.now().timestamp(),
            error_message=f"Failed to start job: {str(e)}",
        )

        db.update_job(conn=ctx.db, job=failed_job)
        logger.error(format.format_job_action(failed_job, action="failed"))

    remaining = len(db.list_jobs(conn=ctx.db, status="queued"))
    logger.info(f"Processed jobs from queue; remaining queued jobs: {remaining}")


@db.safe_transaction
async def start_queued_jobs(ctx: context.NexusServerContext) -> None:
    await _for_queued_jobs(ctx)


@exc.handle_exception_async(Exception, message="Health check encountered an error", reraise=False)
async def check_system_health() -> None:
    health_result = system.check_health(force_refresh=False)
    if health_result.status == "unhealthy":
        logger.warning(f"System health is UNHEALTHY: score {health_result.score}")


@exc.handle_exception_async(Exception, message="Scheduler encountered an error", reraise=False)
async def scheduler_loop(ctx: context.NexusServerContext) -> None:
    while True:
        await update_running_jobs(ctx=ctx)
        await update_wandb_urls(ctx=ctx)
        await start_queued_jobs(ctx=ctx)
        await check_system_health()

        await asyncio.sleep(ctx.config.refresh_rate)
