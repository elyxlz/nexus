import asyncio
import pathlib
import contextlib
import datetime as dt
import importlib.metadata
import os
import typing

import fastapi as fa
import uvicorn

from nexus.service import models
from nexus.service.config import load_config
from nexus.service.git import cleanup_repo, validate_git_url
from nexus.service.format import format_job_action
from nexus.service.gpu import get_gpus
from nexus.service.job import (
    create_job,
    get_job_logs,
    kill_job,
)
from nexus.service.logger import logger
from nexus.service.scheduler import job_scheduler
from nexus.service.state import (
    add_jobs_to_state,
    load_state,
    remove_jobs_from_state,
    save_state,
    update_jobs_in_state,
)

# Service Setup
config = load_config()
state = load_state(config.state_path)


@contextlib.asynccontextmanager
async def lifespan(app: fa.FastAPI):
    scheduler_task = asyncio.create_task(job_scheduler(state, config))
    logger.info("Nexus service started")
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    save_state(state, state_path=config.state_path)
    logger.info("Nexus service stopped")


app = fa.FastAPI(
    title="Nexus GPU Job Service",
    description="GPU Job Management Service",
    version=importlib.metadata.version("nexusai"),
    lifespan=lifespan,
)


# Service Endpoints
@app.get("/v1/service/status", response_model=models.ServiceStatusResponse)
async def get_status():
    gpus = get_gpus(state)
    queued = sum(1 for j in state.jobs if j.status == "queued")
    running = sum(1 for j in state.jobs if j.status == "running")
    completed = sum(1 for j in state.jobs if j.status == "completed")

    return models.ServiceStatusResponse(
        running=True,
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        completed_jobs=completed,
        is_paused=state.is_paused,
    )


@app.get("/v1/service/logs", response_model=models.ServiceLogsResponse)
async def get_service_logs():
    try:
        nexus_dir = pathlib.Path.home() / ".nexus"
        log_path = nexus_dir / "service.log"
        return models.ServiceLogsResponse(logs=log_path.read_text() if log_path.exists() else "")
    except Exception as e:
        raise fa.HTTPException(status_code=500, detail=str(e))


@app.post("/v1/service/pause", response_model=models.ServiceActionResponse)
async def pause_service():
    state.is_paused = True
    save_state(state, state_path=config.state_path)
    logger.info("Service paused")
    return models.ServiceActionResponse(status="paused")


@app.post("/v1/service/resume", response_model=models.ServiceActionResponse)
async def resume_service():
    state.is_paused = False
    save_state(state, state_path=config.state_path)
    logger.info("Service resumed")
    return models.ServiceActionResponse(status="resumed")


# Job Endpoints
@app.get("/v1/jobs", response_model=list[models.Job])
async def list_jobs(
    status: typing.Literal["queued", "running", "completed", "failed"] | None = None,
    gpu_index: int | None = None,
):
    filtered_jobs = state.jobs
    if status:
        filtered_jobs = [j for j in filtered_jobs if j.status == status]
    if gpu_index is not None:
        filtered_jobs = [j for j in filtered_jobs if j.gpu_index == gpu_index]
    return filtered_jobs


@app.post("/v1/jobs", response_model=list[models.Job])
async def add_jobs(job_request: models.JobsRequest):
    """Add multiple jobs to the queue with git repository information"""
    if not validate_git_url(job_request.git_repo_url):
        raise fa.HTTPException(status_code=400, detail=f"Invalid git repository URL: {job_request.git_repo_url}")

    try:
        jobs = [
            create_job(command=command, git_repo_url=job_request.git_repo_url, git_tag=job_request.git_tag) for command in job_request.commands
        ]

        add_jobs_to_state(state, jobs=jobs)

        for job in jobs:
            logger.info(format_job_action(job, action="added"))

        logger.info(f"Added {len(jobs)} new jobs")

        return jobs

    except Exception as e:
        logger.error(f"Error adding jobs: {e}")
        raise fa.HTTPException(status_code=500, detail=str(e))


@app.get("/v1/jobs/{job_id}", response_model=models.Job)
async def get_job(job_id: str):
    job = next((j for j in state.jobs if j.id == job_id), None)
    if not job:
        raise fa.HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(job_id: str):
    job = next((j for j in state.jobs if j.id == job_id), None)
    if not job:
        raise fa.HTTPException(status_code=404, detail="Job not found")

    logs = get_job_logs(job, jobs_dir=config.jobs_dir)
    return models.JobLogsResponse(logs=logs or "")


@app.delete("/v1/jobs/running", response_model=models.JobActionResponse)
async def kill_running_jobs(job_ids: list[str]):
    killed = []
    failed = []
    killed_jobs = []

    for job_id in job_ids:
        job = next((j for j in state.jobs if j.id == job_id), None)
        if not job:
            failed.append({"id": job_id, "error": "Job not found"})
            continue

        if job.status != "running":
            failed.append({"id": job_id, "error": "Job is not running"})
            continue

        try:
            kill_job(job, jobs_dir=config.jobs_dir)
            job.status = "failed"
            job.completed_at = dt.datetime.now().timestamp()
            job.error_message = "Killed by user"
            killed.append(job.id)
            killed_jobs.append(job)
            cleanup_repo(config.jobs_dir, job_id=job.id)

        except Exception as e:
            logger.error(f"Failed to kill job {job.id}: {e}")
            failed.append({"id": job_id, "error": str(e)})

    if killed_jobs:
        update_jobs_in_state(state, jobs=killed_jobs)
        save_state(state, state_path=config.state_path)

    return models.JobActionResponse(killed=killed, failed=failed)


@app.post("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpus(gpu_indexes: list[int]):
    blacklisted = []
    failed = []

    for gpu_index in gpu_indexes:
        if gpu_index in state.blacklisted_gpus:
            failed.append({"index": gpu_index, "error": "GPU already blacklisted"})
        else:
            state.blacklisted_gpus.append(gpu_index)
            blacklisted.append(gpu_index)
            logger.info(f"Blacklisted GPU {gpu_index}")

    if blacklisted:
        save_state(state, state_path=config.state_path)

    return models.GpuActionResponse(blacklisted=blacklisted, failed=failed, removed=None)


@app.delete("/v1/gpus/blacklist", response_model=models.GpuActionResponse)
async def remove_gpu_blacklist(gpu_indexes: list[int]):
    removed = []
    failed = []

    for gpu_index in gpu_indexes:
        if gpu_index not in state.blacklisted_gpus:
            failed.append({"index": gpu_index, "error": "GPU not in blacklist"})
        else:
            state.blacklisted_gpus.remove(gpu_index)
            removed.append(gpu_index)
            logger.info(f"Removed GPU {gpu_index} from blacklist")

    if removed:
        save_state(state, state_path=config.state_path)

    return models.GpuActionResponse(removed=removed, failed=failed, blacklisted=None)


@app.delete("/v1/jobs/queued", response_model=models.JobQueueActionResponse)
async def remove_queued_jobs(job_ids: list[str]):
    removed = []
    failed = []

    for job_id in job_ids:
        job = next((j for j in state.jobs if j.id == job_id), None)
        if not job:
            failed.append({"id": job_id, "error": "Job not found"})
            continue

        if job.status != "queued":
            failed.append({"id": job_id, "error": "Job is not queued"})
            continue

        removed.append(job_id)

    if removed:
        remove_jobs_from_state(state, job_ids=removed)

    return models.JobQueueActionResponse(removed=removed, failed=failed)


# GPU Endpoints
@app.get("/v1/gpus", response_model=list[models.GpuInfo])
async def list_gpus():
    return get_gpus(state)


@app.post("/v1/service/stop", response_model=models.ServiceActionResponse)
async def stop_service():
    logger.info("Service shutdown initiated by API request")

    # Schedule shutdown after a brief delay to allow response completion
    asyncio.create_task(shutdown_service())

    return models.ServiceActionResponse(status="stopping")


async def shutdown_service():
    await asyncio.sleep(1)  # Delay to allow response to complete
    os._exit(0)  # Forcefully terminate the service


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return {"detail": str(exc)}, 500


def main():
    config = load_config()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
