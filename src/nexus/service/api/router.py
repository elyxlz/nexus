import asyncio
import dataclasses as dc
import getpass
import importlib.metadata
import os
import pathlib as pl

import fastapi as fa

from nexus.service import job
from nexus.service.core import context, db, models
from nexus.service.integrations import git, gpu
from nexus.service.utils import format

router = fa.APIRouter()


def get_context(request: fa.Request) -> context.NexusServiceContext:
    return request.app.state.ctx


@router.get("/v1/service/status", response_model=models.ServiceStatusResponse)
async def get_status(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    # Get all jobs from the database and count statuses
    all_jobs = db.list_jobs(ctx.db)
    queued = sum(1 for j in all_jobs if j.status == "queued")
    running = sum(1 for j in all_jobs if j.status == "running")
    completed = sum(1 for j in all_jobs if j.status in ("completed", "failed"))
    # For GPU info, pass the list of running jobs and blacklisted GPUs
    running_jobs = [j for j in all_jobs if j.status == "running"]
    blacklisted = db.list_blacklisted_gpus(ctx.db)
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
    nexus_dir: pl.Path = pl.Path.home() / ".nexus_service"
    log_path: pl.Path = nexus_dir / "service.log"
    logs: str = log_path.read_text() if log_path.exists() else ""
    ctx.logger.info(f"Service logs retrieved, size: {len(logs)} characters")
    return models.ServiceLogsResponse(logs=logs)


@router.get("/v1/jobs", response_model=list[models.Job])
async def list_jobs(
    status: str | None = None,
    gpu_index: int | None = None,
    ctx: context.NexusServiceContext = fa.Depends(get_context),
):
    jobs = db.list_jobs(ctx.db, status=status)
    if gpu_index is not None:
        jobs = [j for j in jobs if j.gpu_index == gpu_index]
    ctx.logger.info(f"Found {len(jobs)} jobs matching criteria")
    return jobs


@router.post("/v1/jobs", response_model=list[models.Job])
async def add_jobs(
    job_request: models.JobsRequest,
    ctx: context.NexusServiceContext = fa.Depends(get_context),
):
    norm_url: str = git.normalize_git_url(job_request.git_repo_url)
    new_jobs = [
        job.create_job(
            command=command,
            git_repo_url=norm_url,
            git_tag=job_request.git_tag,
            user=job_request.user,
            discord_id=job_request.discord_id,
        )
        for command in job_request.commands
    ]
    for j in new_jobs:
        db.add_job(ctx.db, j)
        ctx.logger.info(format.format_job_action(j, action="added"))
    ctx.db.commit()
    ctx.logger.info(f"Added {len(new_jobs)} new jobs")
    return new_jobs


@router.get("/v1/jobs/{job_id}", response_model=models.Job)
async def get_job(job_id: str, ctx: context.NexusServiceContext = fa.Depends(get_context)):
    job_instance = db.get_job(ctx.db, job_id)
    if not job_instance:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise fa.HTTPException(status_code=404, detail="Job not found")
    ctx.logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(job_id: str, ctx: context.NexusServiceContext = fa.Depends(get_context)):
    _job = db.get_job(ctx.db, job_id)
    if not _job:
        ctx.logger.warning(f"Job not found: {job_id}")
        raise fa.HTTPException(status_code=404, detail="Job not found")
    logs = job.get_job_logs(_job.dir)
    logs = logs or ""
    ctx.logger.info(f"Retrieved logs for job {job_id}, size: {len(logs)} characters")
    return models.JobLogsResponse(logs=logs)


@router.delete("/v1/jobs/running", response_model=models.JobActionResponse)
async def kill_running_jobs(job_ids: list[str], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    killed: list[str] = []
    failed: list[dict] = []
    for job_id in job_ids:
        _job = db.get_job(ctx.db, job_id)
        if not _job:
            failed.append({"id": job_id, "error": "Job not found"})
        elif _job.status != "running":
            failed.append({"id": _job.id, "error": "Job is not running"})
        else:
            updated = dc.replace(_job, marked_for_kill=True)
            db.update_job(ctx.db, updated)
            killed.append(_job.id)
            ctx.logger.info(f"Marked job {_job.id} for termination")
    ctx.db.commit()
    return models.JobActionResponse(killed=killed, failed=failed)


@router.delete("/v1/jobs/queued", response_model=models.JobQueueActionResponse)
async def remove_queued_jobs(job_ids: list[str], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    removed: list[str] = []
    failed: list[dict] = []
    for job_id in job_ids:
        success = db.delete_queued_job(ctx.db, job_id)
        if not success:
            failed.append({"id": job_id, "error": "Job not found or not queued"})
        else:
            removed.append(job_id)
            ctx.logger.info(f"Removed queued job {job_id}")
    ctx.db.commit()
    return models.JobQueueActionResponse(removed=removed, failed=failed)


@router.post("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpus(gpu_indexes: list[int], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    successful = []
    failed = []
    for _gpu in gpu_indexes:
        added = db.add_blacklisted_gpu(ctx.db, _gpu)
        if added:
            successful.append(_gpu)
            ctx.logger.info(f"Blacklisted GPU {_gpu}")
        else:
            failed.append({"index": _gpu, "error": "GPU already blacklisted"})
    ctx.db.commit()
    return models.GpuActionResponse(blacklisted=successful, failed=failed, removed=None)


@router.delete("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def remove_gpu_blacklist(gpu_indexes: list[int], ctx: context.NexusServiceContext = fa.Depends(get_context)):
    removed = []
    failed = []
    for _gpu in gpu_indexes:
        removed_flag = db.remove_blacklisted_gpu(ctx.db, _gpu)
        if removed_flag:
            removed.append(_gpu)
            ctx.logger.info(f"Removed GPU {_gpu} from blacklist")
        else:
            failed.append({"index": _gpu, "error": "GPU not in blacklist"})
    ctx.db.commit()
    return models.GpuActionResponse(removed=removed, failed=failed, blacklisted=None)


@router.get("/v1/gpus", response_model=list[models.GpuInfo])
async def list_gpus(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    running_jobs = db.list_jobs(ctx.db, status="running")
    blacklisted = db.list_blacklisted_gpus(ctx.db)
    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )
    ctx.logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.post("/v1/service/stop", response_model=models.ServiceActionResponse)
async def stop_service(ctx: context.NexusServiceContext = fa.Depends(get_context)):
    ctx.logger.info("Service shutdown initiated by API request")
    asyncio.create_task(shutdown_service())
    return models.ServiceActionResponse(status="stopping")


async def shutdown_service():
    await asyncio.sleep(1)
    os._exit(0)
