import asyncio
import dataclasses as dc
import datetime as dt
import pathlib as pl
import tempfile

from nexus.server.core import config, context, db, job
from nexus.server.integrations import git, gpu, notifications, wandb_finder
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
            await git.async_cleanup_git_tag(ctx.logger, git_tag=_job.git_tag, git_repo_url=_job.git_repo_url)

        elif not job.is_job_running(ctx.logger, job=_job):
            updated_job = await job.async_end_job(ctx.logger, _job=_job, killed=False)
            await job.async_cleanup_job_repo(ctx.logger, job_dir=_job.dir)
            await git.async_cleanup_git_tag(ctx.logger, git_tag=_job.git_tag, git_repo_url=_job.git_repo_url)

        else:
            continue

        if updated_job.status != "running":
            action = "completed" if updated_job.status == "completed" else "failed"
            msg = format.format_job_action(updated_job, action=action)
            ctx.logger.info(msg) if action == "completed" else ctx.logger.error(msg)

            if _job.notifications:
                await notifications.notify_job_action(ctx.logger, job=_job, action=action)

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

    if not queued_jobs:
        ctx.logger.debug("No jobs in queue")
        return

    available_gpus = [
        g
        for g in gpu.get_gpus(
            ctx.logger,
            running_jobs=db.list_jobs(ctx.logger, conn=ctx.db, status="running"),
            blacklisted_gpus=db.list_blacklisted_gpus(ctx.logger, conn=ctx.db),
            mock_gpus=ctx.config.mock_gpus,
        )
        if gpu.is_gpu_available(g)
    ]

    if not available_gpus:
        running_count = len(db.list_jobs(ctx.logger, conn=ctx.db, status="running"))
        ctx.logger.debug(f"No available GPUs. {running_count} jobs running")

    for gpu_instance in available_gpus:
        if not queued_jobs:
            break

        _job = queued_jobs.pop(0)

        jobs_dir = pl.Path(tempfile.mkdtemp())
        if ctx.config.server_dir is not None:
            jobs_dir = config.get_jobs_dir(ctx.config.server_dir)

        _job = dc.replace(_job, dir=jobs_dir / _job.id)
        started = await job.async_start_job(ctx.logger, job=_job, gpu_index=gpu_instance.index)

        db.update_job(ctx.logger, conn=ctx.db, job=started)
        ctx.logger.info(format.format_job_action(started, action="started"))

        if started.notifications:
            job_with_notification = await notifications.notify_job_action(ctx.logger, job=started, action="started")
            db.update_job(ctx.logger, conn=ctx.db, job=job_with_notification)

    remaining = len(db.list_jobs(ctx.logger, conn=ctx.db, status="queued"))
    ctx.logger.info(f"Started jobs on available GPUs; remaining queued jobs: {remaining}")


async def scheduler_loop(ctx: context.NexusServerContext):
    while True:
        try:
            await update_running_jobs(ctx=ctx)
            await update_wandb_urls(ctx=ctx)
            await start_queued_jobs(ctx=ctx)

        except Exception:
            ctx.logger.exception("Scheduler encountered an error:")

        await asyncio.sleep(ctx.config.refresh_rate)
