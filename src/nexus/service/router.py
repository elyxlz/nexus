import asyncio
import contextlib
import getpass
import importlib.metadata
import os
import pathlib as pl
import shutil
import traceback
from collections.abc import AsyncGenerator

import fastapi as fa
import uvicorn
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from nexus.service import config, job, logger, models, scheduler, state, git, gpu, format


router = fa.APIRouter()


def get_state(request: Request) -> models.NexusServiceState:
    return request.app.state.state


def get_config(request: Request) -> config.NexusServiceConfig:
    return request.app.state.config


@router.get("/v1/service/status", response_model=models.ServiceStatusResponse)
async def get_status(
    _state: models.NexusServiceState = Depends(get_state), _config: config.NexusServiceConfig = Depends(get_config)
):
    gpus = gpu.get_gpus(_state, mock_gpus=_config.mock_gpus)
    queued = sum(1 for j in _state.jobs if j.status == "queued")
    running = sum(1 for j in _state.jobs if j.status == "running")
    completed = sum(1 for j in _state.jobs if j.status == "completed")
    response = models.ServiceStatusResponse(
        running=True,
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        completed_jobs=completed,
        service_user=getpass.getuser(),
        service_version=importlib.metadata.version("nexusai"),
    )
    logger.info(f"Service status: {response}")
    return response


@router.get("/v1/service/logs", response_model=models.ServiceLogsResponse)
async def get_service_logs():
    nexus_dir: pl.Path = pl.Path.home() / ".nexus_service"
    log_path: pl.Path = nexus_dir / "service.log"
    logs: str = log_path.read_text() if log_path.exists() else ""
    logger.info(f"Service logs retrieved, size: {len(logs)} characters")
    return models.ServiceLogsResponse(logs=logs)


@router.get("/v1/jobs", response_model=list[models.Job])
async def list_jobs(
    status: str | None = None,
    gpu_index: int | None = None,
    _state: models.NexusServiceState = Depends(get_state),
):
    filtered = list(_state.jobs)
    if status:
        filtered = [j for j in filtered if j.status == status]
    if gpu_index is not None:
        filtered = [j for j in filtered if j.gpu_index == gpu_index]
    logger.info(f"Found {len(filtered)} jobs matching criteria")
    return filtered


@router.post("/v1/jobs", response_model=list[models.Job])
async def add_jobs(
    job_request: models.JobsRequest,
    _state: models.NexusServiceState = Depends(get_state),
    _config: config.NexusServiceConfig = Depends(get_config),
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
    _state.jobs = _state.jobs + tuple(new_jobs)
    for _job in new_jobs:
        logger.info(format.format_job_action(_job, action="added"))
    logger.info(f"Added {len(new_jobs)} new jobs")
    return new_jobs


@router.get("/v1/jobs/{job_id}", response_model=models.Job)
async def get_job(job_id: str, _state: models.NexusServiceState = Depends(get_state)):
    job_instance = next((j for j in _state.jobs if j.id == job_id), None)
    if not job_instance:
        logger.warning(f"Job not found: {job_id}")
        raise HTTPException(status_code=404, detail="Job not found")
    logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(
    job_id: str,
    _state: models.NexusServiceState = Depends(get_state),
    _config: config.NexusServiceConfig = Depends(get_config),
):
    _job = next((j for j in _state.jobs if j.id == job_id), None)
    if not _job:
        raise HTTPException(status_code=404, detail="Job not found")
    logs = job.get_job_logs(_job.id, jobs_dir=config.get_jobs_dir(_config.service_dir))
    return models.JobLogsResponse(logs=logs or "")


@router.delete("/v1/jobs/running", response_model=models.JobActionResponse)
async def kill_running_jobs(job_ids: list[str], _state: models.NexusServiceState = Depends(get_state)):
    killed: list[str] = []
    failed: list[dict] = []
    new_jobs = list(_state.jobs)
    for idx, _job in enumerate(new_jobs):
        if _job.id in job_ids:
            if _job.status != "running":
                failed.append({"id": _job.id, "error": "Job is not running"})
            else:
                updated = __import__("dataclasses").replace(_job, marked_for_kill=True)
                new_jobs[idx] = updated
                killed.append(_job.id)
                logger.info(f"Marked job {_job.id} for termination")
    _state.jobs = tuple(new_jobs)
    return models.JobActionResponse(killed=killed, failed=failed)


@router.post("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpus(
    gpu_indexes: list[int],
    _state: models.NexusServiceState = Depends(get_state),
):
    successful: list[int] = []
    failed: list[models.GpuActionError] = []
    new_list = list(_state.blacklisted_gpus)
    for _gpu in gpu_indexes:
        if _gpu in new_list:
            failed.append(models.GpuActionError(index=_gpu, error="GPU already blacklisted"))
        else:
            new_list.append(_gpu)
            successful.append(_gpu)
    _state.blacklisted_gpus = tuple(new_list)
    return models.GpuActionResponse(blacklisted=successful, failed=failed, removed=None)


@router.delete("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def remove_gpu_blacklist(gpu_indexes: list[int], _state: models.NexusServiceState = Depends(get_state)):
    removed: list[int] = []
    failed: list[models.GpuActionError] = []
    new_list = list(_state.blacklisted_gpus)
    for _gpu in gpu_indexes:
        if _gpu not in new_list:
            failed.append(models.GpuActionError(index=_gpu, error="GPU not in blacklist"))
        else:
            new_list.remove(_gpu)
            removed.append(_gpu)
            logger.info(f"Removed GPU {_gpu} from blacklist")
    _state.blacklisted_gpus = tuple(new_list)
    return models.GpuActionResponse(removed=removed, failed=failed, blacklisted=None)


@router.delete("/v1/jobs/queued", response_model=models.JobQueueActionResponse)
async def remove_queued_jobs(job_ids: list[str], _state: models.NexusServiceState = Depends(get_state)):
    removed: list[str] = []
    failed: list[dict] = []
    remaining: list[models.Job] = []
    for _job in _state.jobs:
        if _job.id in job_ids:
            if _job.status != "queued":
                failed.append({"id": _job.id, "error": "Job is not queued"})
            else:
                removed.append(_job.id)
        else:
            remaining.append(_job)
    _state.jobs = tuple(remaining)
    logger.info(f"Removed {len(removed)} queued jobs; {len(failed)} failed removals")
    return models.JobQueueActionResponse(removed=removed, failed=failed)


@router.get("/v1/gpus", response_model=list[models.GpuInfo])
async def list_gpus(
    _state: models.NexusServiceState = Depends(get_state), _config: config.NexusServiceConfig = Depends(get_config)
):
    gpus = gpu.get_gpus(_state, mock_gpus=_config.mock_gpus)
    logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.post("/v1/service/stop", response_model=models.ServiceActionResponse)
async def stop_service():
    logger.info("Service shutdown initiated by API request")
    asyncio.create_task(shutdown_service())
    return models.ServiceActionResponse(status="stopping")


async def shutdown_service():
    await asyncio.sleep(1)
    os._exit(0)
