import asyncio
import contextlib
import time
import typing

import uvicorn
from fastapi import FastAPI, HTTPException

from nexus.service.config import load_config
from nexus.service.gpu import get_available_gpus, get_gpus
from nexus.service.job import (
    create_job,
    get_job_logs,
    is_job_running,
    kill_job,
    start_job,
)
from nexus.service.logger import logger
from nexus.service.models import GpuInfo, Job, ServiceStatus
from nexus.service.state import (
    add_jobs_to_state,
    clean_old_completed_jobs_in_state,
    load_state,
    remove_jobs_from_state,
    save_state,
    update_jobs_in_state,
)

config = load_config()
state = load_state(config.state_path)


async def job_scheduler():
    """Background task to schedule and monitor jobs"""
    while True:
        if not state.is_paused:
            try:
                # update running job statuses
                jobs_to_update = []
                for job in state.jobs:
                    if job.status == "running" and not is_job_running(job):
                        job.status = "completed"
                        job.completed_at = time.time()
                        jobs_to_update.append(job)
                        logger.info(f"Job {job.id} completed")

                if jobs_to_update:
                    update_jobs_in_state(
                        state, jobs=jobs_to_update, state_path=config.state_path
                    )

                # Clean old completed jobs
                clean_old_completed_jobs_in_state(
                    state,
                    state_path=config.state_path,
                    max_completed=config.history_limit,
                )

                # Get available GPUs and running jobs
                available_gpus = get_available_gpus(state)

                # Start new jobs
                jobs_to_update = []
                for gpu in available_gpus:
                    queued_jobs = [j for j in state.jobs if j.status == "queued"]
                    if queued_jobs:
                        job = queued_jobs[0]
                        try:
                            start_job(job, gpu_index=gpu.index, log_dir=config.log_dir)
                            job.status = "running"
                            jobs_to_update.append(job)
                            logger.info(
                                f"Started job {job.id} with command '{job.command}' on GPU {gpu.index}"
                            )
                        except Exception as e:
                            job.status = "failed"
                            job.error_message = str(e)
                            job.completed_at = time.time()
                            jobs_to_update.append(job)
                            logger.error(f"Failed to start job {job.id}: {e}")

                if jobs_to_update:
                    update_jobs_in_state(
                        state, jobs=jobs_to_update, state_path=config.state_path
                    )

            except Exception as e:
                logger.error(f"Scheduler error: {e}")

        await asyncio.sleep(config.refresh_rate)


# Startup and Shutdown
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    scheduler_task = asyncio.create_task(job_scheduler())
    logger.info("Nexus service started")
    yield
    # Shutdown
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    save_state(state, state_path=config.state_path)
    logger.info("Nexus service stopped")


app = FastAPI(
    title="Nexus GPU Job Service",
    description="GPU Job Management Service",
    version="1.0.0",
    lifespan=lifespan,
)


# System Status Endpoints
@app.get("/v1/service/status", response_model=ServiceStatus)
async def get_status():
    """Get current service status"""
    gpus = get_gpus()
    queued = sum(1 for j in state.jobs if j.status == "queued")
    running = sum(1 for j in state.jobs if j.status == "running")

    return ServiceStatus(
        running=True,
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        is_paused=state.is_paused,
    )


@app.get("/v1/service/logs")
async def get_service_logs():
    """Get service logs"""
    try:
        log_path = config.log_dir / "service.log"
        if log_path.exists():
            return {"logs": log_path.read_text()}
        return {"logs": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/service/pause")
async def pause_service():
    """Pause job processing"""
    state.is_paused = True
    save_state(state, state_path=config.state_path)
    logger.info("Service paused")
    return {"status": "paused"}


@app.post("/v1/service/resume")
async def resume_service():
    """Resume job processing"""
    state.is_paused = False
    save_state(state, state_path=config.state_path)
    logger.info("Service resumed")
    return {"status": "resumed"}


# Job Management Endpoints
@app.get("/v1/jobs", response_model=list[Job])
async def list_jobs(
    status: typing.Literal["queued", "running", "completed"] | None = None,
    gpu_index: int | None = None,
):
    """Get all jobs with optional filtering"""
    filtered_jobs = state.jobs
    if status:
        filtered_jobs = [j for j in filtered_jobs if j.status == status]
    if gpu_index is not None:
        filtered_jobs = [j for j in filtered_jobs if j.gpu_index == gpu_index]
    return filtered_jobs


@app.post("/v1/jobs", response_model=list[Job])
async def add_jobs(commands: list[str]):
    """Add new jobs to the queue"""
    jobs = []
    for command in commands:
        job = create_job(command)
        jobs.append(job)

    add_jobs_to_state(state, jobs=jobs, state_path=config.state_path)
    logger.info(f"Added {len(jobs)} jobs to queue")
    return jobs


@app.get("/v1/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str):
    """Get details for a specific job"""
    job = next((j for j in state.jobs if j.id == job_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/v1/jobs/{job_id}/logs")
async def get_job_logs_endpoint(job_id: str):
    """Get logs for a specific job"""
    job = next((j for j in state.jobs if j.id == job_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    stdout, stderr = get_job_logs(job, log_dir=config.log_dir)
    return {"stdout": stdout or "", "stderr": stderr or ""}


@app.post("/v1/jobs/kill")
async def kill_jobs(job_ids: list[str]):
    """Kill running jobs by their IDs"""
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
            kill_job(job)
            job.status = "failed"
            job.completed_at = time.time()
            job.error_message = "Killed by user"
            killed.append(job.id)
            killed_jobs.append(job)
        except Exception as e:
            logger.error(f"Failed to kill job {job.id}: {e}")
            failed.append({"id": job_id, "error": str(e)})

    if killed_jobs:
        update_jobs_in_state(state, jobs=killed_jobs, state_path=config.state_path)

    return {"killed": killed, "failed": failed}


@app.post("/v1/jobs/remove_from_queue")
async def remove_jobs_from_queue(job_ids: list[str]):
    """Remove jobs from queue by their IDs"""
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
        remove_jobs_from_state(state, job_ids=removed, state_path=config.state_path)

    return {"removed": removed, "failed": failed}


# GPU Management Endpoints
@app.get("/v1/gpus", response_model=list[GpuInfo])
async def list_gpus():
    """Get information about all GPUs"""
    gpus = get_gpus()
    for gpu in gpus:
        gpu.is_blacklisted = gpu.index in state.blacklisted_gpus
        running_job = next(
            (
                j
                for j in state.jobs
                if j.status == "running" and j.gpu_index == gpu.index
            ),
            None,
        )
        gpu.running_job_id = running_job.id if running_job else None
    return gpus


@app.post("/v1/gpus/{gpu_index}/blacklist")
async def blacklist_gpu(gpu_index: int):
    """Add a GPU to the blacklist"""
    if gpu_index in state.blacklisted_gpus:
        raise HTTPException(status_code=400, detail="GPU already blacklisted")

    state.blacklisted_gpus.append(gpu_index)
    save_state(state, state_path=config.state_path)
    logger.info(f"Blacklisted GPU {gpu_index}")
    return {"status": "success"}


@app.delete("/v1/gpus/{gpu_index}/blacklist")
async def remove_gpu_blacklist(gpu_index: int):
    """Remove a GPU from the blacklist"""
    if gpu_index not in state.blacklisted_gpus:
        raise HTTPException(status_code=400, detail="GPU not in blacklist")

    state.blacklisted_gpus.remove(gpu_index)
    save_state(state, state_path=config.state_path)
    logger.info(f"Removed GPU {gpu_index} from blacklist")
    return {"status": "success"}


# Error Handlers
@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handle any unhandled exceptions"""
    logger.error(f"Unhandled exception: {exc}")
    return {"detail": str(exc)}, 500


def main():
    """Entry point for the nexus-service CLI command"""
    config = load_config()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
