import asyncio
import dataclasses as dc
import getpass
import importlib.metadata
import logging.handlers
import os
import pathlib as pl

import fastapi as fa

from nexus.service.api import models
from nexus.service.core import context, db, job, schemas
from nexus.service.core import exceptions as exc
from nexus.service.integrations import git, gpu
from nexus.service.utils import format

__all__ = [
    "router",
    "get_context",
    "get_status",
    "get_service_logs",
    "list_jobs",
    "add_jobs",
    "get_job",
    "get_job_logs_endpoint",
    "kill_running_jobs",
    "remove_queued_jobs",
    "blacklist_gpus",
    "remove_gpu_blacklist",
    "list_gpus",
    "stop_service",
]

router = fa.APIRouter()


def get_context(request: fa.Request) -> context.NexusServiceContext:
    return request.app.state.ctx


@router.get("/v1/service/status", response_model=models.ServiceStatusResponse)
async def get_status(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    # Get jobs from the database with specific status filters
    queued_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="queued")
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
    completed_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="completed")
    failed_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="failed")

    # Count jobs by status directly using database queries
    queued = len(queued_jobs)
    running = len(running_jobs)
    completed = len(completed_jobs) + len(failed_jobs)

    # For GPU info, pass the list of running jobs and blacklisted GPUs
    blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)
    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )

    response = models.ServiceStatusResponse(
        running=True,
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        completed_jobs=completed,
        service_user=getpass.getuser(),
        service_version=importlib.metadata.version("nexusai"),
    )
    ctx.logger.info(f"Service status: {response}")
    return response


@router.get("/v1/service/logs", response_model=models.ServiceLogsResponse)
async def get_service_logs(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    logs: str = ""

    # Get log file path from the logger's file handler
    for handler in ctx.logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            log_path = pl.Path(handler.baseFilename)
            if log_path.exists():
                logs = log_path.read_text()
                break

    if not logs:
        ctx.logger.warning("Could not retrieve log content from logger handlers")

    ctx.logger.info(f"Service logs retrieved, size: {len(logs)} characters")
    return models.ServiceLogsResponse(logs=logs)


@router.get("/v1/jobs", response_model=list[schemas.Job])
async def list_jobs(
    status: str | None = None,
    gpu_index: int | None = None,
    ctx: context.NexusServiceContext = fa.Depends(get_context),
):
    jobs = db.list_jobs(ctx.logger, conn=ctx.db, status=status)
    if gpu_index is not None:
        jobs = [j for j in jobs if j.gpu_index == gpu_index]
    ctx.logger.info(f"Found {len(jobs)} jobs matching criteria")
    return jobs


@db.safe_transaction
@router.post("/v1/jobs", response_model=list[schemas.Job])
async def add_jobs(job_request: models.JobsRequest, ctx: context.NexusServiceContext = fa.Depends(get_context)):
    if not git.validate_git_url(job_request.git_repo_url):
        ctx.logger.error(f"Invalid git URL format: {job_request.git_repo_url}")
        raise exc.GitError(message="Invalid git repository URL format")

    if not job_request.commands:
        ctx.logger.error("No commands provided in job request")
        raise exc.JobError(message="No commands provided to create jobs")

    norm_url = git.normalize_git_url(job_request.git_repo_url)

    new_jobs = []
    for command in job_request.commands:
        # Create job instance
        j = job.create_job(
            command=command,
            git_repo_url=norm_url,
            git_tag=job_request.git_tag,
            user=job_request.user,
            discord_id=job_request.discord_id,
        )

        db.add_job(ctx.logger, conn=ctx.db, job=j)
        ctx.logger.info(format.format_job_action(j, action="added"))
        new_jobs.append(j)

    ctx.logger.info(f"Added {len(new_jobs)} new jobs")
    return new_jobs


@router.get("/v1/jobs/{job_id}", response_model=schemas.Job)
async def get_job(job_id: str, ctx: context.NexusServiceContext = fa.Depends(get_context)):
    job_instance = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    if not job_instance:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise exc.JobError(message=f"Job not found: {job_id}")
    ctx.logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(job_id: str, ctx: context.NexusServiceContext = fa.Depends(get_context)):
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    if not _job:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise exc.JobError(message=f"Job not found: {job_id}")

    logs = job.get_job_logs(ctx.logger, job_dir=_job.dir)
    logs = logs or ""
    ctx.logger.info(f"Retrieved logs for job {job_id}, size: {len(logs)} characters")
    return models.JobLogsResponse(logs=logs)


@db.safe_transaction
@router.delete("/v1/jobs/running", response_model=models.JobActionResponse)
async def kill_running_jobs(job_ids: list[str], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    if not job_ids:
        raise exc.JobError(message="No job IDs provided")

    killed: list[str] = []
    failed: list[models.JobActionError] = []

    for job_id in job_ids:
        try:
            _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
            if not _job:
                failed.append(models.JobActionError(id=job_id, error="Job not found"))
                continue

            if _job.status != "running":
                failed.append(
                    models.JobActionError(id=_job.id, error=f"Job is not running (current status: {_job.status})")
                )
                continue

            updated = dc.replace(_job, marked_for_kill=True)
            db.update_job(ctx.logger, conn=ctx.db, job=updated)
            killed.append(_job.id)
            ctx.logger.info(f"Marked job {_job.id} for termination")

        except exc.JobError as e:
            if "not found" in str(e).lower():
                failed.append(models.JobActionError(id=job_id, error="Job not found"))
            else:
                failed.append(models.JobActionError(id=job_id, error=e.message))
        except Exception as e:
            ctx.logger.error(f"Unexpected error killing job {job_id}: {e}")
            failed.append(models.JobActionError(id=job_id, error=f"Internal error: {str(e)}"))

    return models.JobActionResponse(killed=killed, failed=failed)


@db.safe_transaction
@router.delete("/v1/jobs/queued", response_model=models.JobQueueActionResponse)
async def remove_queued_jobs(job_ids: list[str], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    if not job_ids:
        raise exc.JobError(message="No job IDs provided")

    removed: list[str] = []
    failed: list[models.JobQueueActionError] = []

    for job_id in job_ids:
        try:
            db.delete_queued_job(ctx.logger, conn=ctx.db, job_id=job_id)
            removed.append(job_id)
            ctx.logger.info(f"Removed queued job {job_id}")
        except exc.JobError as e:
            if "not found" in str(e).lower():
                failed.append(models.JobQueueActionError(id=job_id, error="Job not found"))
            else:
                failed.append(models.JobQueueActionError(id=job_id, error=e.message))
        except Exception as e:
            ctx.logger.error(f"Unexpected error removing job {job_id}: {e}")
            failed.append(models.JobQueueActionError(id=job_id, error=f"Internal error: {str(e)}"))

    return models.JobQueueActionResponse(removed=removed, failed=failed)


@db.safe_transaction
@router.post("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpus(gpu_indexes: list[int], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    if not gpu_indexes:
        raise exc.GPUError(message="No GPU indexes provided")

    successful: list[int] = []
    failed: list[models.GpuActionError] = []

    for _gpu in gpu_indexes:
        try:
            added = db.add_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_index=_gpu)
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
async def remove_gpu_blacklist(gpu_indexes: list[int], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    if not gpu_indexes:
        raise exc.GPUError(message="No GPU indexes provided")

    removed: list[int] = []
    failed: list[models.GpuActionError] = []

    for _gpu in gpu_indexes:
        try:
            removed_flag = db.remove_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_index=_gpu)
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
async def list_gpus(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
    blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)

    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )

    ctx.logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.post("/v1/service/stop", response_model=models.ServiceActionResponse)
async def stop_service(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    async def shutdown_service():
        await asyncio.sleep(1)
        os._exit(0)

    ctx.logger.info("Service shutdown initiated by API request")
    asyncio.create_task(shutdown_service())
    return models.ServiceActionResponse(status="stopping")
