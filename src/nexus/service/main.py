import asyncio
import contextlib
import datetime as dt
import pathlib
import typing

import fastapi as fa
import uvicorn

from nexus.service import models
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
from nexus.service.state import (
    add_jobs_to_state,
    clean_old_completed_jobs_in_state,
    load_state,
    remove_jobs_from_state,
    save_state,
    update_jobs_in_state,
)

# Service Setup
config = load_config()
state = load_state(config.state_path)


async def job_scheduler():
    while True:
        if not state.is_paused:
            try:
                jobs_to_update = []
                for job in state.jobs:
                    if job.status == "running" and not is_job_running(job):
                        job.status = "completed"
                        job.completed_at = dt.datetime.now().timestamp()
                        jobs_to_update.append(job)
                        logger.info(f"Job {job.id} completed")

                if jobs_to_update:
                    update_jobs_in_state(
                        state, jobs=jobs_to_update, state_path=config.state_path
                    )

                clean_old_completed_jobs_in_state(
                    state,
                    state_path=config.state_path,
                    max_completed=config.history_limit,
                )

                available_gpus = get_available_gpus(state)

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
                            job.completed_at = dt.datetime.now().timestamp()
                            jobs_to_update.append(job)
                            logger.error(f"Failed to start job {job.id}: {e}")

                if jobs_to_update:
                    update_jobs_in_state(
                        state, jobs=jobs_to_update, state_path=config.state_path
                    )

            except Exception as e:
                logger.error(f"Scheduler error: {e}")

        await asyncio.sleep(config.refresh_rate)


@contextlib.asynccontextmanager
async def lifespan(app: fa.FastAPI):
    scheduler_task = asyncio.create_task(job_scheduler())
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
    version="1.0.0",
    lifespan=lifespan,
)


# Service Endpoints
@app.get("/v1/service/status", response_model=models.ServiceStatusResponse)
async def get_status():
    gpus = get_gpus()
    queued = sum(1 for j in state.jobs if j.status == "queued")
    running = sum(1 for j in state.jobs if j.status == "running")

    return models.ServiceStatusResponse(
        running=True,
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        is_paused=state.is_paused,
    )


@app.get("/v1/service/logs", response_model=models.ServiceLogsResponse)
async def get_service_logs():
    try:
        log_path = config.log_dir / "service.log"
        return models.ServiceLogsResponse(
            logs=log_path.read_text() if log_path.exists() else ""
        )
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
    status: typing.Literal["queued", "running", "completed"] | None = None,
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
    """Add multiple jobs to the queue, all sharing the same working directory"""
    # Validate working directory
    working_dir = pathlib.Path(job_request.working_dir)
    if not working_dir.is_absolute() or not working_dir.exists():
        raise fa.HTTPException(
            status_code=400,
            detail=f"Working directory '{job_request.working_dir}' must be an absolute path and must exist",
        )

    jobs = [
        create_job(command, working_dir=working_dir) for command in job_request.commands
    ]
    add_jobs_to_state(state, jobs=jobs, state_path=config.state_path)
    logger.info(f"Added {len(jobs)} jobs to queue (working_dir: {working_dir})")
    return jobs


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

    stdout, stderr = get_job_logs(job, log_dir=config.log_dir)
    return models.JobLogsResponse(stdout=stdout or "", stderr=stderr or "")


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
            kill_job(job)
            job.status = "failed"
            job.completed_at = dt.datetime.now().timestamp()
            job.error_message = "Killed by user"
            killed.append(job.id)
            killed_jobs.append(job)
        except Exception as e:
            logger.error(f"Failed to kill job {job.id}: {e}")
            failed.append({"id": job_id, "error": str(e)})

    if killed_jobs:
        update_jobs_in_state(state, jobs=killed_jobs, state_path=config.state_path)

    return models.JobActionResponse(killed=killed, failed=failed)


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
        remove_jobs_from_state(state, job_ids=removed, state_path=config.state_path)

    return models.JobQueueActionResponse(removed=removed, failed=failed)


# GPU Endpoints
@app.get("/v1/gpus", response_model=list[models.GpuInfo])
async def list_gpus():
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


@app.post("/v1/gpus/{gpu_index}/blacklist", response_model=models.GpuActionResponse)
async def blacklist_gpu(gpu_index: int):
    if gpu_index in state.blacklisted_gpus:
        raise fa.HTTPException(status_code=400, detail="GPU already blacklisted")

    state.blacklisted_gpus.append(gpu_index)
    save_state(state, state_path=config.state_path)
    logger.info(f"Blacklisted GPU {gpu_index}")
    return models.GpuActionResponse(status="success")


@app.delete("/v1/gpus/{gpu_index}/blacklist", response_model=models.GpuActionResponse)
async def remove_gpu_blacklist(gpu_index: int):
    if gpu_index not in state.blacklisted_gpus:
        raise fa.HTTPException(status_code=400, detail="GPU not in blacklist")

    state.blacklisted_gpus.remove(gpu_index)
    save_state(state, state_path=config.state_path)
    logger.info(f"Removed GPU {gpu_index} from blacklist")
    return models.GpuActionResponse(status="success")


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return {"detail": str(exc)}, 500


def main():
    config = load_config()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
