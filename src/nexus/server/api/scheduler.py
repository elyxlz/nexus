import asyncio
import dataclasses as dc
import datetime as dt

from nexus.server.core import context, db, job
from nexus.server.integrations import gpu, notifications, wandb_finder
from nexus.server.utils import format

__all__ = ["scheduler_loop"]


@db.safe_transaction
async def update_running_jobs(ctx: context.NexusServerContext) -> None:
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")

    for _job in running_jobs:
        updated_job = _job

        if _job.marked_for_kill and job.is_job_running(ctx.logger, job=_job):
            await job.kill_job(ctx.logger, job=_job)
            updated_job = await job.async_end_job(ctx.logger, _job=_job, killed=True)
            await job.async_cleanup_job_repo(ctx.logger, job_dir=_job.dir)

        elif not job.is_job_running(ctx.logger, job=_job):
            updated_job = await job.async_end_job(ctx.logger, _job=_job, killed=False)
            await job.async_cleanup_job_repo(ctx.logger, job_dir=_job.dir)

        else:
            continue

        if updated_job.status != "running":
            if updated_job.status == "completed":
                action = "completed"
            elif updated_job.status == "killed":
                action = "killed"
            else:
                action = "failed"

            ctx.logger.info(format.format_job_action(updated_job, action=action))

            if _job.notifications:
                await notifications.notify_job_action(ctx.logger, _job=_job, action=action)

        db.update_job(ctx.logger, conn=ctx.db, job=updated_job)


@db.safe_transaction
async def update_wandb_urls(ctx: context.NexusServerContext) -> None:
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")

    for _job in running_jobs:
        if _job.wandb_url or _job.started_at is None or not _job.search_wandb:
            continue

        if dt.datetime.now().timestamp() - _job.started_at > 720:
            continue

        wandb_url = await wandb_finder.find_wandb_run_by_nexus_id(ctx.logger, job=_job)

        if wandb_url:
            updated = dc.replace(_job, wandb_url=wandb_url)
            db.update_job(ctx.logger, conn=ctx.db, job=updated)
            ctx.logger.info(f"Associated job {_job.id} with W&B run: {wandb_url}")
            await notifications.update_notification_with_wandb(ctx.logger, job=updated)


@db.safe_transaction
async def start_queued_jobs(ctx: context.NexusServerContext) -> None:
    queued_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="queued")
    queued_jobs = job.get_queue(queued_jobs)

    if not queued_jobs:
        ctx.logger.debug("No jobs in queue")
        return

    _job = queued_jobs[0]

    all_gpus = gpu.get_gpus(
        ctx.logger,
        running_jobs=db.list_jobs(ctx.logger, conn=ctx.db, status="running"),
        blacklisted_gpus=db.list_blacklisted_gpus(ctx.logger, conn=ctx.db),
        mock_gpus=ctx.config.mock_gpus,
    )

    available_gpus = [g for g in all_gpus if gpu.is_gpu_available(g, ignore_blacklist=_job.ignore_blacklist)]

    if not available_gpus:
        ctx.logger.debug("No available GPUs")
        return

    available_gpu_idxs = [g.index for g in available_gpus]

    if _job.gpu_idxs:
        if all(idx in available_gpu_idxs for idx in _job.gpu_idxs):
            job_gpu_idxs = _job.gpu_idxs
            ctx.logger.info(f"Using user-specified GPU indices {job_gpu_idxs} for job {_job.id}")
        else:
            unavailable_gpus = [idx for idx in _job.gpu_idxs if idx not in available_gpu_idxs]
            ctx.logger.debug(
                f"Job {_job.id} requires specific GPU indices {_job.gpu_idxs}, but indices {unavailable_gpus} are unavailable"
            )
            return
    elif _job.num_gpus <= len(available_gpu_idxs):
        job_gpu_idxs = available_gpu_idxs[: _job.num_gpus]
    else:
        return

    try:
        # Try to start the job
        started = await job.async_start_job(
            ctx.logger, job=_job, gpu_idxs=job_gpu_idxs, server_dir=ctx.config.server_dir
        )

        db.update_job(ctx.logger, conn=ctx.db, job=started)
        ctx.logger.info(format.format_job_action(started, action="started"))

        if started.notifications:
            job_with_notification = await notifications.notify_job_action(ctx.logger, _job=started, action="started")
            db.update_job(ctx.logger, conn=ctx.db, job=job_with_notification)

    except Exception as e:
        ctx.logger.error(f"Failed to start job {_job.id}: {str(e)}")

        failed_job = dc.replace(
            _job,
            status="failed",
            completed_at=dt.datetime.now().timestamp(),
            error_message=f"Failed to start job: {str(e)}",
        )

        db.update_job(ctx.logger, conn=ctx.db, job=failed_job)
        ctx.logger.error(format.format_job_action(failed_job, action="failed"))

    remaining = len(db.list_jobs(ctx.logger, conn=ctx.db, status="queued"))
    ctx.logger.info(f"Processed jobs from queue; remaining queued jobs: {remaining}")


async def scheduler_loop(ctx: context.NexusServerContext):
    while True:
        try:
            await update_running_jobs(ctx=ctx)
            await update_wandb_urls(ctx=ctx)
            await start_queued_jobs(ctx=ctx)

        except Exception:
            ctx.logger.exception("Scheduler encountered an error:")

        await asyncio.sleep(ctx.config.refresh_rate)
