import dataclasses as dc
import getpass
import importlib.metadata
import logging.handlers
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
    request: models.JobListRequest = fa.Depends(),
    ctx: context.NexusServerContext = fa.Depends(_get_context),
):
    jobs = db.list_jobs(ctx.logger, conn=ctx.db, status=request.status, command_regex=request.command_regex)
    if request.gpu_index is not None:
        jobs = [j for j in jobs if request.gpu_index in j.gpu_idxs]
    paginated_jobs = jobs[request.offset : request.offset + request.limit]
    ctx.logger.info(f"Found {len(paginated_jobs)} jobs matching criteria")

    if request.status == "queued":
        paginated_jobs = job.get_queue(paginated_jobs)

    return paginated_jobs


@db.safe_transaction
@router.post("/v1/jobs", response_model=schemas.Job, status_code=201)
async def create_job_endpoint(
    job_request: models.JobRequest, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    norm_url = git.normalize_git_url(job_request.git_repo_url)

    priority = job_request.priority if not job_request.run_immediately else 9999
    ignore_blacklist = job_request.run_immediately

    gpu_idxs_list = job_request.gpu_idxs or []
    if job_request.run_immediately:
        running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
        blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)
        all_gpus = gpu.get_gpus(
            ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
        )

        available_gpus = [g for g in all_gpus if gpu.is_gpu_available(g, ignore_blacklist=ignore_blacklist)]

        if gpu_idxs_list:
            requested_gpus = [g for g in all_gpus if g.index in gpu_idxs_list]
            if len(requested_gpus) != len(gpu_idxs_list):
                missing = set(gpu_idxs_list) - {g.index for g in requested_gpus}
                raise exc.GPUError(message=f"Requested GPUs not found: {missing}")
            if any(not gpu.is_gpu_available(g, ignore_blacklist=ignore_blacklist) for g in requested_gpus):
                unavailable = [
                    g.index for g in requested_gpus if not gpu.is_gpu_available(g, ignore_blacklist=ignore_blacklist)
                ]
                raise exc.GPUError(message=f"Requested GPUs are not available: {unavailable}")
        elif job_request.num_gpus > len(available_gpus):
            raise exc.GPUError(
                message=f"Requested {job_request.num_gpus} GPUs but only {len(available_gpus)} are available"
            )

    j = job.create_job(
        command=job_request.command,
        git_repo_url=norm_url,
        git_tag=job_request.git_tag,
        git_branch=job_request.git_branch,
        user=job_request.user,
        num_gpus=job_request.num_gpus,
        priority=priority,
        gpu_idxs=job_request.gpu_idxs,
        env=job_request.env,
        jobrc=job_request.jobrc,
        search_wandb=job_request.search_wandb,
        notifications=job_request.notifications,
        node_name=ctx.config.node_name,
        ignore_blacklist=ignore_blacklist,
    )

    db.add_job(ctx.logger, conn=ctx.db, job=j)
    ctx.logger.info(format.format_job_action(j, action="added"))
    ctx.logger.info(f"Added new job: {j.id}")
    return j


@router.get("/v1/jobs/{job_id}", response_model=schemas.Job)
async def get_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    job_instance = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    ctx.logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(
    job_id: str, last_n_lines: int | None = None, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)
    logs = await job.async_get_job_logs(ctx.logger, job_dir=_job.dir, last_n_lines=last_n_lines) or ""
    ctx.logger.info(f"Retrieved logs for job {job_id}, size: {len(logs)} characters")
    return models.JobLogsResponse(logs=logs)


@db.safe_transaction
@router.delete("/v1/jobs/{job_id}", status_code=204)
async def delete_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    """Delete a job if queued. For running jobs, use the /kill endpoint."""
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)

    if _job.status != "queued":
        raise exc.InvalidJobStateError(
            message=f"Cannot delete job {job_id} with status '{_job.status}'. Only queued jobs can be deleted."
        )

    db.delete_queued_job(ctx.logger, conn=ctx.db, job_id=job_id)
    ctx.logger.info(f"Removed queued job {job_id}")


@db.safe_transaction
@router.post("/v1/jobs/{job_id}/kill", status_code=204)
async def kill_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    """Kill a running job. Cannot be used for queued jobs."""
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)

    if _job.status != "running":
        raise exc.InvalidJobStateError(
            message=f"Cannot kill job {job_id} with status '{_job.status}'. Only running jobs can be killed."
        )

    updated = dc.replace(_job, marked_for_kill=True)
    db.update_job(ctx.logger, conn=ctx.db, job=updated)
    ctx.logger.info(f"Marked running job {job_id} for termination")


@db.safe_transaction
@router.patch("/v1/jobs/{job_id}", response_model=schemas.Job)
async def update_job_endpoint(
    job_id: str, job_update: models.JobUpdateRequest, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    _job = db.get_job(ctx.logger, conn=ctx.db, job_id=job_id)

    if _job.status != "queued":
        raise exc.InvalidJobStateError(
            message=f"Cannot update job {job_id} with status '{_job.status}'. Only queued jobs can be updated."
        )

    update_fields = {}

    if job_update.command is not None:
        update_fields["command"] = job_update.command

    if job_update.priority is not None:
        update_fields["priority"] = job_update.priority

    if not update_fields:
        return _job

    updated = dc.replace(_job, **update_fields)
    db.update_job(ctx.logger, conn=ctx.db, job=updated)
    ctx.logger.info(format.format_job_action(updated, action="updated"))

    return updated


@db.safe_transaction
@router.put("/v1/gpus/{gpu_idx}/blacklist", response_model=models.GpuStatusResponse)
async def blacklist_gpu_endpoint(gpu_idx: int, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    changed = db.add_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_idx=gpu_idx)
    if changed:
        ctx.logger.info(f"Blacklisted GPU {gpu_idx}")
    else:
        ctx.logger.info(f"GPU {gpu_idx} already blacklisted")
    return models.GpuStatusResponse(gpu_idx=gpu_idx, blacklisted=True, changed=changed)


@db.safe_transaction
@router.delete("/v1/gpus/{gpu_idx}/blacklist", response_model=models.GpuStatusResponse)
async def remove_gpu_blacklist_endpoint(gpu_idx: int, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    changed = db.remove_blacklisted_gpu(ctx.logger, conn=ctx.db, gpu_idx=gpu_idx)
    if changed:
        ctx.logger.info(f"Removed GPU {gpu_idx} from blacklist")
    else:
        ctx.logger.info(f"GPU {gpu_idx} already not blacklisted")
    return models.GpuStatusResponse(gpu_idx=gpu_idx, blacklisted=False, changed=changed)


@router.get("/v1/gpus", response_model=list[gpu.GpuInfo])
async def list_gpus_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    running_jobs = db.list_jobs(ctx.logger, conn=ctx.db, status="running")
    blacklisted = db.list_blacklisted_gpus(ctx.logger, conn=ctx.db)
    gpus = gpu.get_gpus(
        ctx.logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
    )
    ctx.logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.get("/v1/health", response_model=models.HealthResponse)
async def health_check_endpoint(
    detailed: bool = False, refresh: bool = False, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    if not detailed:
        return models.HealthResponse()

    health_result = system.check_health(force_refresh=refresh)
    return models.HealthResponse(
        alive=True,
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
