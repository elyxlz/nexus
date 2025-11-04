import asyncio
import dataclasses as dc
import datetime as dt
import typing as tp

from nexus.server.core import context, db, job, exceptions as exc
from nexus.server.external import gpu, notifications, wandb_finder, system
from nexus.server.utils import format, logger

__all__ = ["scheduler_loop"]


async def _for_running(ctx: context.NexusServerContext):
    for _job in db.list_jobs(ctx.db, status="running"):
        is_running = job.is_job_running(job=_job)
        if is_running and not _job.marked_for_kill:
            continue

        killed = _job.marked_for_kill and is_running
        if killed:
            await job.kill_job(job=_job)

        updated_job = await job.async_end_job(_job=_job, killed=killed)
        await job.async_cleanup_job_repo(job_dir=_job.dir)

        job_action: tp.Literal["completed", "failed", "killed"] = "failed"
        if updated_job.status in ["completed", "killed"]:
            job_action = tp.cast(tp.Literal["completed", "killed"], updated_job.status)
        logger.info(format.format_job_action(updated_job, action=job_action))

        if _job.notifications:
            await notifications.notify_job_action(_job=updated_job, action=job_action)

        db.update_job(conn=ctx.db, job=updated_job)


@db.safe_transaction
async def update_running_jobs(ctx: context.NexusServerContext) -> None:
    await _for_running(ctx)


async def _for_wandb_urls(ctx: context.NexusServerContext):
    for _job in db.list_jobs(ctx.db, status="running"):
        if (
            _job.wandb_url
            or _job.started_at is None
            or "wandb" not in _job.integrations
            or dt.datetime.now().timestamp() - _job.started_at > 720
        ):
            continue

        if url := await wandb_finder.find_wandb_run_by_nexus_id(job=_job):
            updated = dc.replace(_job, wandb_url=url)
            db.update_job(conn=ctx.db, job=updated)
            await notifications.update_notification_with_wandb(job=updated)


@db.safe_transaction
async def update_wandb_urls(ctx: context.NexusServerContext) -> None:
    await _for_wandb_urls(ctx)


async def _for_queued_jobs(ctx: context.NexusServerContext):
    queued_jobs = db.list_jobs(ctx.db, status="queued")
    if not queued_jobs:
        return

    ordered_jobs = job.get_queue(queued_jobs)
    if not ordered_jobs:
        return

    for _job in ordered_jobs:
        if _job.num_gpus == 0:
            gpu_idxs = []
        else:
            running_jobs = db.list_jobs(conn=ctx.db, status="running")
            blacklisted = db.list_blacklisted_gpus(conn=ctx.db)
            all_gpus = gpu.get_gpus(
                running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
            )

            available = [
                g
                for g in all_gpus
                if gpu.is_gpu_available(g, ignore_blacklist=_job.ignore_blacklist, required=_job.gpu_idxs)
            ]

            if not available:
                continue

            avail_idxs = [g.index for g in available]
            if _job.gpu_idxs and all(idx in avail_idxs for idx in _job.gpu_idxs):
                gpu_idxs = _job.gpu_idxs
            elif not _job.gpu_idxs and _job.num_gpus <= len(avail_idxs):
                gpu_idxs = avail_idxs[: _job.num_gpus]
            else:
                continue

        try:
            started = await job.async_start_job(job=_job, gpu_idxs=gpu_idxs, ctx=ctx)
            db.update_job(conn=ctx.db, job=started)
            logger.info(format.format_job_action(started, action="started"))

            if _job.artifact_id and not db.is_artifact_in_use(conn=ctx.db, artifact_id=_job.artifact_id):
                try:
                    db.delete_artifact(conn=ctx.db, artifact_id=_job.artifact_id)
                    logger.info(f"Deleted artifact {_job.artifact_id} as it's no longer needed")
                except Exception as e:
                    logger.error(f"Failed to delete artifact {_job.artifact_id}: {str(e)}")

            if started.notifications:
                job_with_notif = await notifications.notify_job_action(_job=started, action="started")
                db.update_job(conn=ctx.db, job=job_with_notif)

            if _job.num_gpus > 0:
                break
        except Exception as e:
            db.update_job(
                conn=ctx.db,
                job=dc.replace(
                    _job,
                    status="failed",
                    completed_at=dt.datetime.now().timestamp(),
                    error_message=f"Failed to start job: {str(e)}",
                ),
            )
            logger.error(f"Failed to start job {_job.id}: {str(e)}")


@db.safe_transaction
async def start_queued_jobs(ctx: context.NexusServerContext) -> None:
    await _for_queued_jobs(ctx)


@exc.handle_exception(Exception, message="Health check error", reraise=False)
async def check_system_health() -> None:
    health = system.check_health(force_refresh=False)
    if health.status == "unhealthy":
        logger.warning(f"System health is UNHEALTHY: score {health.score}")


@exc.handle_exception(Exception, message="Scheduler error", reraise=False)
async def scheduler_loop(ctx: context.NexusServerContext) -> None:
    while True:
        await update_running_jobs(ctx=ctx)
        await update_wandb_urls(ctx=ctx)
        await start_queued_jobs(ctx=ctx)
        await check_system_health()
        await asyncio.sleep(ctx.config.refresh_rate)
