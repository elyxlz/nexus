import asyncio
import dataclasses as dc
import datetime as dt
import pathlib as pl
import tempfile

from nexus.service import job
from nexus.service.core import config, context, db
from nexus.service.integrations import git, gpu, wandb_finder, webhooks
from nexus.service.utils import format

__all__ = ["scheduler_loop"]


@db.safe_transaction
async def update_running_jobs(ctx: context.NexusServiceContext) -> None:
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")

    for _job in running_jobs:
        updated_job = _job

        if _job.marked_for_kill and job.is_job_session_running(ctx.logger, job_id=_job.id):
            job.kill_job_session(ctx.logger, job_id=_job.id)
            updated_job = job.end_job(ctx.logger, _job=_job, killed=True)
            await git.async_cleanup_repo(ctx.logger, job_dir=_job.dir)

        elif not job.is_job_session_running(ctx.logger, job_id=_job.id):
            updated_job = job.end_job(ctx.logger, _job=_job, killed=False)
            await git.async_cleanup_repo(ctx.logger, job_dir=_job.dir)

        else:
            continue

        if updated_job.status != "running":
            action = "completed" if updated_job.status == "completed" else "failed"

            if action == "completed":
                ctx.logger.info(format.format_job_action(updated_job, action=action))
            else:
                ctx.logger.error(format.format_job_action(updated_job, action=action))

            other_running = db.list_jobs(ctx.logger, conn=ctx.db, status="running")

            if not any(j.git_tag == updated_job.git_tag for j in other_running):
                await git.async_cleanup_git_tag(ctx.logger, git_tag=_job.git_tag, git_repo_url=_job.git_repo_url)

            if ctx.config.webhooks_enabled:
                if action == "completed":
                    await webhooks.notify_job_completed(ctx.logger, job=_job)
                elif action == "failed":
                    job_logs = job.get_job_logs(ctx.logger, job_dir=_job.dir, last_n_lines=20)
                    await webhooks.notify_job_failed(ctx.logger, job=_job, job_logs=job_logs)

            if action == "failed":
                last_lines = job.get_job_logs(ctx.logger, job_dir=_job.dir, last_n_lines=20)
                if last_lines:
                    ctx.logger.error(f"Last 20 lines of job log:\n{''.join(last_lines)}")
        db.update_job(conn=ctx.db, job=updated_job)


@db.safe_transaction
async def update_wandb_urls(ctx: context.NexusServiceContext) -> None:
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")

    for _job in running_jobs:
        if _job.wandb_url or _job.started_at is None:
            continue

        runtime = dt.datetime.now().timestamp() - _job.started_at

        if runtime > 720:
            continue

        wandb_url = wandb_finder.find_wandb_run_by_nexus_id(ctx.logger, dirs=[str(_job.dir)], nexus_job_id=_job.id)

        if wandb_url:
            updated = dc.replace(_job, wandb_url=wandb_url)
            db.update_job(conn=ctx.db, job=updated)
            ctx.logger.info(f"Associated job {_job.id} with W&B run: {wandb_url}")

            if ctx.config.webhooks_enabled:
                await webhooks.update_job_wandb(ctx.logger, job=updated)


@db.safe_transaction
async def start_queued_jobs(ctx: context.NexusServiceContext) -> None:
    queued_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="queued")

    if not queued_jobs:
        ctx.logger.debug("No jobs in queue")
        return

    available_gpus = [
        g
        for g in gpu.get_gpus(
            ctx.logger,
            running_jobs=db.list_jobs(ctx.logger, conn=ctx.db, status="running"),
            blacklisted_gpus=db.list_blacklisted_gpus(ctx.db),
            mock_gpus=ctx.config.mock_gpus,
        )
        if gpu.is_gpu_available(g)
    ]

    if not available_gpus:
        running_count = len(db.list_jobs(ctx.logger, conn=ctx.db, status="running"))
        ctx.logger.debug(f"No available GPUs. {running_count} jobs running")
        return

    for gpu_instance in available_gpus:
        if not queued_jobs:
            break

        _job = queued_jobs.pop(0)

        jobs_dir = pl.Path(tempfile.mkdtemp())
        if ctx.config.service_dir is not None:
            jobs_dir = config.get_jobs_dir(ctx.config.service_dir)

        _job = dc.replace(_job, dir=jobs_dir / _job.id)
        started = await job.async_start_job(
            ctx.logger,
            job=_job,
            gpu_index=gpu_instance.index,
            github_token=ctx.env.github_token,
            job_env=ctx.env.model_dump(),
        )

        db.update_job(conn=ctx.db, job=started)
        ctx.logger.info(format.format_job_action(started, action="started"))

        if ctx.config.webhooks_enabled:
            await webhooks.notify_job_started(ctx.logger, job=started)

    ctx.db.commit()
    remaining = len(db.list_jobs(ctx.logger, conn=ctx.db, status="queued"))
    ctx.logger.info(f"Started jobs on available GPUs; remaining queued jobs: {remaining}")


async def scheduler_loop(ctx: context.NexusServiceContext):
    while True:
        try:
            await update_running_jobs(ctx=ctx)
            await update_wandb_urls(ctx=ctx)
            await start_queued_jobs(ctx=ctx)

        except Exception:
            ctx.logger.exception("Scheduler encountered an error:")

        await asyncio.sleep(ctx.config.refresh_rate)
