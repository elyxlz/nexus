import dataclasses as dc
import getpass
import importlib.metadata
import os

import base58
import fastapi as fa

from nexus.server.api import models
from nexus.server.core import context, db, job, schemas
from nexus.server.core import exceptions as exc
from nexus.server.external import gpu, system
from nexus.server.utils import format, logger

__all__ = ["router"]

router = fa.APIRouter()


def _get_context(request: fa.Request) -> context.NexusServerContext:
    return request.app.state.ctx


@router.get("/v1/server/status", response_model=models.ServerStatusResponse)
async def get_status_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    queued_jobs = db.list_jobs(conn=ctx.db, status="queued")
    running_jobs = db.list_jobs(conn=ctx.db, status="running")
    completed_jobs = db.list_jobs(conn=ctx.db, status="completed")
    failed_jobs = db.list_jobs(conn=ctx.db, status="failed")

    queued = len(queued_jobs)
    running = len(running_jobs)
    completed = len(completed_jobs) + len(failed_jobs)

    blacklisted = db.list_blacklisted_gpus(conn=ctx.db)
    gpus = gpu.get_gpus(running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus)

    response = models.ServerStatusResponse(
        gpu_count=len(gpus),
        queued_jobs=queued,
        running_jobs=running,
        completed_jobs=completed,
        server_user=getpass.getuser(),
        server_version=importlib.metadata.version("nexusai"),
        node_name=ctx.config.node_name,
    )
    logger.info(f"Server status: {response}")
    return response


@router.get("/v1/jobs", response_model=list[schemas.Job])
async def list_jobs_endpoint(
    request: models.JobListRequest = fa.Depends(),
    ctx: context.NexusServerContext = fa.Depends(_get_context),
):
    jobs = db.list_jobs(conn=ctx.db, status=request.status, command_regex=request.command_regex)
    if request.gpu_index is not None:
        jobs = [j for j in jobs if request.gpu_index in j.gpu_idxs]
    paginated_jobs = jobs[request.offset : request.offset + request.limit]
    logger.info(f"Found {len(paginated_jobs)} jobs matching criteria")

    if request.status == "queued":
        paginated_jobs = job.get_queue(paginated_jobs)

    return paginated_jobs


@router.get("/v1/artifacts/by-sha/{git_sha}", response_model=models.ArtifactCheckResponse)
async def check_artifact_by_sha(git_sha: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    artifact_id = db.get_artifact_id_by_git_sha(ctx.db, git_sha)
    return models.ArtifactCheckResponse(exists=artifact_id is not None, artifact_id=artifact_id)


@db.safe_transaction
@router.post("/v1/artifacts", response_model=models.ArtifactUploadResponse, status_code=201)
async def upload_artifact(
    request: fa.Request, git_sha: str | None = None, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    raw = await request.body()
    if not raw:
        raise exc.InvalidRequestError("Empty artifact upload")

    max_size_mb = 50
    max_size_bytes = max_size_mb * 1024 * 1024
    if len(raw) > max_size_bytes:
        raise exc.InvalidRequestError(f"Artifact exceeds maximum size of {max_size_mb} MB")

    artifact_id = base58.b58encode(os.urandom(6)).decode()
    db.add_artifact(ctx.db, artifact_id, raw, git_sha)
    return models.ArtifactUploadResponse(data=artifact_id)


@db.safe_transaction
@router.post("/v1/jobs", response_model=schemas.Job, status_code=201)
async def create_job_endpoint(
    job_request: models.JobRequest, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    priority = job_request.priority if not job_request.run_immediately else 9999
    ignore_blacklist = job_request.ignore_blacklist

    gpu_idxs_list = job_request.gpu_idxs or []
    if job_request.run_immediately and job_request.num_gpus > 0:
        running_jobs = db.list_jobs(conn=ctx.db, status="running")
        blacklisted = db.list_blacklisted_gpus(conn=ctx.db)

        if gpu_idxs_list:
            all_gpus = gpu.get_gpus(
                running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus
            )
            requested_gpus = [g for g in all_gpus if g.index in gpu_idxs_list]
            if len(requested_gpus) != len(gpu_idxs_list):
                missing = set(gpu_idxs_list) - {g.index for g in requested_gpus}
                raise exc.GPUError(message=f"Requested GPUs not found: {missing}")

        all_gpus = gpu.get_gpus(running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus)
        available_gpus = [
            g
            for g in all_gpus
            if gpu.is_gpu_available(
                g, ignore_blacklist=ignore_blacklist, required=gpu_idxs_list if gpu_idxs_list else None
            )
        ]

        if gpu_idxs_list and not available_gpus:
            raise exc.GPUError(message=f"Requested GPUs are not available: {gpu_idxs_list}")
        elif job_request.num_gpus > len(available_gpus):
            raise exc.GPUError(
                message=f"Requested {job_request.num_gpus} GPUs but only {len(available_gpus)} are available"
            )

    j = job.create_job(
        command=job_request.command,
        artifact_id=job_request.artifact_id,
        user=job_request.user,
        num_gpus=job_request.num_gpus,
        priority=priority,
        gpu_idxs=job_request.gpu_idxs,
        env=job_request.env,
        jobrc=job_request.jobrc,
        integrations=job_request.integrations,
        notifications=job_request.notifications,
        node_name=ctx.config.node_name,
        git_repo_url=job_request.git_repo_url,
        git_branch=job_request.git_branch,
        git_tag=job_request.git_tag,
        ignore_blacklist=ignore_blacklist,
        job_id=job_request.job_id,
    )

    if job_request.git_tag:
        new_env = dict(j.env)
        new_env.setdefault("NEXUS_GIT_TAG", job_request.git_tag)
        j = dc.replace(j, env=new_env)

    db.add_job(conn=ctx.db, job=j)
    logger.info(format.format_job_action(j, action="added"))

    return db.get_job(conn=ctx.db, job_id=j.id)


@router.get("/v1/jobs/{job_id}", response_model=schemas.Job)
async def get_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    job_instance = db.get_job(conn=ctx.db, job_id=job_id)
    logger.info(f"Job found: {job_instance}")
    return job_instance


@router.get("/v1/jobs/{job_id}/logs", response_model=models.JobLogsResponse)
async def get_job_logs_endpoint(
    job_id: str, last_n_lines: int | None = None, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    _job = db.get_job(conn=ctx.db, job_id=job_id)
    logs = await job.async_get_job_logs(job_dir=_job.dir, last_n_lines=last_n_lines) or ""
    logger.info(f"Retrieved logs for job {job_id}, size: {len(logs)} characters")
    return models.JobLogsResponse(data=logs)


@db.safe_transaction
@router.delete("/v1/jobs/{job_id}", status_code=204)
async def delete_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    """Delete a job if queued. For running jobs, use the /kill endpoint."""
    _job = db.get_job(conn=ctx.db, job_id=job_id)

    if _job.status != "queued":
        raise exc.InvalidJobStateError(
            message=f"Cannot delete job {job_id} with status '{_job.status}'. Only queued jobs can be deleted."
        )

    db.delete_queued_job(conn=ctx.db, job_id=job_id)
    logger.info(f"Removed queued job {job_id}")


@db.safe_transaction
@router.post("/v1/jobs/{job_id}/kill", status_code=204)
async def kill_job_endpoint(job_id: str, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    _job = db.get_job(conn=ctx.db, job_id=job_id)

    if _job.status != "running":
        raise exc.InvalidJobStateError(
            message=f"Cannot kill job {job_id} with status '{_job.status}'. Only running jobs can be killed."
        )

    updated = dc.replace(_job, marked_for_kill=True)
    db.update_job(conn=ctx.db, job=updated)
    logger.info(f"Marked running job {job_id} for termination")


@db.safe_transaction
@router.patch("/v1/jobs/{job_id}", response_model=schemas.Job)
async def update_job_endpoint(
    job_id: str, job_update: models.JobUpdateRequest, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    _job = db.get_job(conn=ctx.db, job_id=job_id)

    update_fields = {}

    if job_update.git_tag is not None:
        update_fields["git_tag"] = job_update.git_tag

    if _job.status == "queued":
        if job_update.command is not None:
            update_fields["command"] = job_update.command

        if job_update.priority is not None:
            update_fields["priority"] = job_update.priority

        if job_update.num_gpus is not None:
            update_fields["num_gpus"] = job_update.num_gpus
    elif job_update.command is not None or job_update.priority is not None or job_update.num_gpus is not None:
        raise exc.InvalidJobStateError(
            message=f"Cannot update command/priority/num_gpus for job {job_id} with status '{_job.status}'. Only queued jobs can be updated."
        )

    if not update_fields:
        return _job

    updated = dc.replace(_job, **update_fields)
    db.update_job(conn=ctx.db, job=updated)
    logger.info(format.format_job_action(updated, action="updated"))

    return updated


@db.safe_transaction
@router.put("/v1/gpus/{gpu_idx}/blacklist", response_model=models.GpuStatusResponse)
async def blacklist_gpu_endpoint(gpu_idx: int, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    changed = db.add_blacklisted_gpu(conn=ctx.db, gpu_idx=gpu_idx)
    if changed:
        logger.info(f"Blacklisted GPU {gpu_idx}")
    else:
        logger.info(f"GPU {gpu_idx} already blacklisted")
    return models.GpuStatusResponse(gpu_idx=gpu_idx, blacklisted=True, changed=changed)


@db.safe_transaction
@router.delete("/v1/gpus/{gpu_idx}/blacklist", response_model=models.GpuStatusResponse)
async def remove_gpu_blacklist_endpoint(gpu_idx: int, ctx: context.NexusServerContext = fa.Depends(_get_context)):
    changed = db.remove_blacklisted_gpu(conn=ctx.db, gpu_idx=gpu_idx)
    if changed:
        logger.info(f"Removed GPU {gpu_idx} from blacklist")
    else:
        logger.info(f"GPU {gpu_idx} already not blacklisted")
    return models.GpuStatusResponse(gpu_idx=gpu_idx, blacklisted=False, changed=changed)


@router.get("/v1/gpus", response_model=list[gpu.GpuInfo])
async def list_gpus_endpoint(ctx: context.NexusServerContext = fa.Depends(_get_context)):
    running_jobs = db.list_jobs(conn=ctx.db, status="running")
    blacklisted = db.list_blacklisted_gpus(conn=ctx.db)
    gpus = gpu.get_gpus(running_jobs=running_jobs, blacklisted_gpus=blacklisted, mock_gpus=ctx.config.mock_gpus)
    logger.info(f"Found {len(gpus)} GPUs")
    return gpus


@router.get("/v1/health", response_model=models.HealthResponse)
async def health_check_endpoint(
    detailed: bool = False, refresh: bool = False, ctx: context.NexusServerContext = fa.Depends(_get_context)
):
    if not detailed:
        return models.HealthResponse()

    # Only force refresh if explicitly requested, otherwise use cached results
    # The system module has its own caching mechanism
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
