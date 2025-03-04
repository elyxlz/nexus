import asyncio
import dataclasses as dc
import getpass
import importlib.metadata
import logging.handlers
import os
import pathlib as pl

import fastapi as fa

from nexus.server.api import models
from nexus.server.core import context, db, job, schemas
from nexus.server.core import exceptions as exc
from nexus.server.integrations import git, gpu, system
from nexus.server.utils import format

__all__ = ["router"]

router = fa.APIRouter()


def _get_context(request: fa.Request) -> context.NexusServerContext:
    return request.app.state.ctx


@router.get("/v1/server/status", response_model=models.ServerStatusResponse)
async def get_status_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    queued_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="queued")
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
    completed_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="completed")
    failed_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="failed")

    queued = len(queued_jobs)
    running = len(running_jobs)
    completed = len(completed_jobs) + len(failed_jobs)

    blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)
    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )

    response = models.ServerStatusResponse(
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        completed_jobs=completed,
        server_user=getpass.getuser(),
        server_version=importlib.metadata.version("nexusai"),
    )
    ctx.logger.info(f"Server status: {response}")
    return response


@router.get("/v1/server/logs", response_model=models.ServerLogsResponse)
async def get_server_logs_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    logs: str = ""

    for handler in ctx.logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            log_path = pl.Path(handler.baseFilename)
            if log_path.exists():
                logs = log_path.read_text()
                break

    if not logs:
        ctx.logger.warning("Could not retrieve log content from logger handlers")

    ctx.logger.info(f"Server logs retrieved, size: {len(logs)} characters")
    return models.ServerLogsResponse(logs=logs)


@router.get("/v1/jobs", response_model=list[schemas.Job])
async def list_jobs_endpoint(
    status: str | None = None,
    gpu_idx: int | None = None,
    command_regex: str | None = None,
    ctx: context.NexusServerContext = fa.Depends(_get_context),
):
    jobs = db.list_jobs(ctx.logger, conn=ctx.db, status=status, command_regex=command_regex)
    if gpu_idx is not None:
        jobs = [j for j in jobs if gpu_idx in j.gpu_idxs]
    ctx.logger.info(f"Found {len(jobs)} jobs matching criteria")
    return jobs


@router.get("/v1/queue", response_model=list[schemas.Job])
async def get_queue_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    queued_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="queued")
    queue = job.get_queue(queued_jobs)
    ctx.logger.info(f"Returning sorted queue with {len(queue)} jobs")
    return queue


@db.safe_transaction
@router.post("/v1/jobs", response_model=schemas.Job)
async def add_job_endpoint(job_request: models.JobRequest, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    norm_url = git.normalize_git_url(job_request.git_repo_url)

    j = job.create_job(
        command=job_request.command,
        status="queued",
        git_repo_url=norm_url,
        git_tag=job_request.git_tag,
        git_branch=job_request.git_branch,
        user=job_request.user,
        num_gpus=job_request.num_gpus,
        priority=job_request.priority,
        env=job_request.env,
        jobrc=job_request.jobrc,
        search_wandb=job_request.search_wandb,
        notifications=job_request.notifications,
        node_name=ctx.config.node_name,
        gpu_idxs=job_request.gpu_idxs,
        ignore_blacklist=job_request.ignore_blacklist,
    )

    db.add_job(ctx.logger, conn=ctx.db, job=j)
    ctx.logger.info(format.format_job_action(j, action="added"))

    ctx.logger.info(f"Added new job: {j.id}")
    return j


@router.get("/v1/jobs/{job_id}", response_model=schemas.Job)
async def get_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    job_instance = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    if not job_instance:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise exc.JobNotFoundError(message=f"Job not found: {job_id}")
    ctx.logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    if not _job:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise exc.JobNotFoundError(message=f"Job not found: {job_id}")

    logs = await job.async_get_job_logs(ctx.logger, job_dir=_job.dir)
    logs = logs or ""
    ctx.logger.info(f"Retrieved logs for job {job_id}, size: {len(logs)} characters")
    return models.JobLogsResponse(logs=logs)


@db.safe_transaction
@router.delete("/v1/jobs/running", response_model=models.JobActionResponse)
async def kill_running_jobs_endpoint(job_ids: list[str], ctx: context.NexusServerContext = fa.Depends(_get_context)):
    if not job_ids:
        raise exc.JobError(message="No job IDs provided")

    killed: list[str] = []
    failed: list[models.JobActionError] = []

    for job_id in job_ids:
        try:
            _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
            if not _job:
                # We don't raise here to collect multiple errors
                failed.append(models.JobActionError(id=job_id, error="Job not found"))
                continue

            if _job.status != "running":
                error_msg = f"Job is not running (current status: {_job.status})"
                failed.append(models.JobActionError(id=_job.id, error=error_msg))
                continue

            updated = dc.replace(_job, marked_for_kill=True)
            db.update_job(ctx.logger, conn=ctx.db, job=updated)
            killed.append(_job.id)
            ctx.logger.info(f"Marked job {_job.id} for termination")

        except exc.JobNotFoundError as e:
            failed.append(models.JobActionError(id=job_id, error=e.message))
        except exc.InvalidJobStateError as e:
            failed.append(models.JobActionError(id=job_id, error=e.message))
        except exc.JobError as e:
            failed.append(models.JobActionError(id=job_id, error=e.message))
        except Exception as e:
            ctx.logger.error(f"Unexpected error killing job {job_id}: {e}")
            failed.append(models.JobActionError(id=job_id, error=f"Internal error: {str(e)}"))

    return models.JobActionResponse(killed=killed, failed=failed)


@db.safe_transaction
@router.delete("/v1/jobs/queued", response_model=models.JobQueueActionResponse)
async def remove_queued_jobs_endpoint(job_ids: list[str], ctx: context.NexusServerContext = fa.Depends(_get_context)):
    if not job_ids:
        raise exc.JobError(message="No job IDs provided")

    removed: list[str] = []
    failed: list[models.JobQueueActionError] = []

    for job_id in job_ids:
        try:
            db.delete_queued_job(ctx.logger, conn=ctx.db, job_id=job_id)
            removed.append(job_id)
            ctx.logger.info(f"Removed queued job {job_id}")
        except exc.JobNotFoundError as e:
            failed.append(models.JobQueueActionError(id=job_id, error=e.message))
        except exc.InvalidJobStateError as e:
            failed.append(models.JobQueueActionError(id=job_id, error=e.message))
        except exc.JobError as e:
            failed.append(models.JobQueueActionError(id=job_id, error=e.message))
        except Exception as e:
            ctx.logger.error(f"Unexpected error removing job {job_id}: {e}")
            failed.append(models.JobQueueActionError(id=job_id, error=f"Internal error: {str(e)}"))

    return models.JobQueueActionResponse(removed=removed, failed=failed)


@db.safe_transaction
@router.post("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpus_endpoint(gpu_idxs: list[int], ctx: context.NexusServerContext = fa.Depends(_get_context)):
    if not gpu_idxs:
        raise exc.GPUError(message="No GPU idxs provided")

    successful: list[int] = []
    failed: list[models.GpuActionError] = []

    for _gpu in gpu_idxs:
        try:
            added = db.add_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_idx=_gpu)
            if added:
                successful.append(_gpu)
                ctx.logger.info(f"Blacklisted GPU {_gpu}")
            else:
                failed.append(models.GpuActionError(index=_gpu, error="GPU already blacklisted"))
        except exc.GPUError as e:
            failed.append(models.GpuActionError(index=_gpu, error=e.message))

    return models.GpuActionResponse(blacklisted=successful, failed=failed, removed=None)


@db.safe_transaction
@router.delete("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def remove_gpu_blacklist_endpoint(
    gpu_idxs: list[int], ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    if not gpu_idxs:
        raise exc.GPUError(message="No GPU idxs provided")

    removed: list[int] = []
    failed: list[models.GpuActionError] = []

    for _gpu in gpu_idxs:
        try:
            removed_flag = db.remove_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_idx=_gpu)
            if removed_flag:
                removed.append(_gpu)
                ctx.logger.info(f"Removed GPU {_gpu} from blacklist")
            else:
                failed.append(models.GpuActionError(index=_gpu, error="GPU not in blacklist"))
        except exc.GPUError as e:
            failed.append(models.GpuActionError(index=_gpu, error=e.message))
        except Exception as e:
            ctx.logger.error(f"Unexpected error removing GPU {_gpu} from blacklist: {e}")
            failed.append(models.GpuActionError(index=_gpu, error=f"Internal error: {str(e)}"))

    return models.GpuActionResponse(removed=removed, failed=failed, blacklisted=None)


@router.get("/v1/gpus", response_model=list[gpu.GpuInfo])
async def list_gpus_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
    blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)

    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )

    ctx.logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.post("/v1/server/stop", response_model=models.ServerActionResponse)
async def stop_server_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    async def shutdown_server():
        await asyncio.sleep(1)
        os._exit(0)

    ctx.logger.info("Server shutdown initiated by API request")
    asyncio.create_task(shutdown_server())
    return models.ServerActionResponse(status="stopping")


@router.get("/v1/health", response_model=models.HealthResponse)
async def health_check_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    health_result = system.check_health()

    return models.HealthResponse(
        status=health_result.status,
        score=health_result.score,
        disk=models.DiskStatsResponse(
            total=health_result.disk.total,
            used=health_result.disk.used,
            free=health_result.disk.free,
            percent_used=health_result.disk.percent_used,
        ),
        network=models.NetworkStatsResponse(
            download_speed=health_result.network.download_speed,
            upload_speed=health_result.network.upload_speed,
            ping=health_result.network.ping,
        ),
        system=models.SystemStatsResponse(
            cpu_percent=health_result.system.cpu_percent,
            memory_percent=health_result.system.memory_percent,
            uptime=health_result.system.uptime,
            load_avg=health_result.system.load_avg,
        ),
    )
