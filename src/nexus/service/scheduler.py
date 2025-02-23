import asyncio
import dataclasses as dc
import datetime as dt
import pathlib as pl
import tempfile

from nexus.service import config, context, format, git, gpu, job, state, wandb_finder, webhooks


async def update_running_jobs(ctx: context.NexusServiceContext) -> None:
    jobs_list = list(ctx.state.jobs)

    for idx, _job in enumerate(jobs_list):
        if _job.status != "running":
            continue

        if _job.marked_for_kill and job.is_job_session_running(_job.id):
            job.kill_job_session(ctx.logger, job_id=_job.id)
            updated = job.end_job(
                ctx.logger, job=_job, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), killed=True
            )
            git.cleanup_repo(ctx.logger, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), job_id=_job.id)

        elif not job.is_job_session_running(_job.id):
            updated = job.end_job(
                ctx.logger, job=_job, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), killed=False
            )
            git.cleanup_repo(ctx.logger, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), job_id=_job.id)

        else:
            continue

        if updated.status != "running":
            action = "completed" if updated.status == "completed" else "failed"

            if action == "completed":
                ctx.logger.info(format.format_job_action(updated, action=action))
            else:
                ctx.logger.error(format.format_job_action(updated, action=action))

            running_jobs = [j for j in jobs_list if j.status == "running"]

            if not any(job.git_tag == updated.git_tag for job in running_jobs):
                git.cleanup_git_tag(ctx.logger, git_tag=_job.git_tag, git_repo_url=_job.git_repo_url)

            if ctx.config.webhooks_enabled:
                if action == "completed":
                    await webhooks.notify_job_completed(ctx.logger, job=_job)
                elif action == "failed":
                    job_logs = job.get_job_logs(
                        _job.id, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), last_n_lines=20
                    )
                    await webhooks.notify_job_failed(ctx.logger, job=_job, job_logs=job_logs)

            if action == "failed":
                last_lines = job.get_job_logs(
                    updated.id, jobs_dir=config.get_jobs_dir(ctx.config.service_dir), last_n_lines=20
                )
                if last_lines:
                    ctx.logger.error(f"Last 20 lines of job log:\n{''.join(last_lines)}")
        jobs_list[idx] = updated
    ctx.state.jobs = tuple(jobs_list)


async def update_wandb_urls(ctx: context.NexusServiceContext) -> None:
    jobs_list = list(ctx.state.jobs)
    for idx, _job in enumerate(jobs_list):
        if _job.status == "running" and not _job.wandb_url:
            assert _job.started_at is not None
            runtime = dt.datetime.now().timestamp() - _job.started_at

            if runtime > 720:
                continue

            job_repo_dir = config.get_jobs_dir(ctx.config.service_dir) / _job.id / "repo"
            if not job_repo_dir.exists():
                continue

            wandb_url = wandb_finder.find_wandb_run_by_nexus_id(
                ctx.logger, dirs=[str(job_repo_dir)], nexus_job_id=_job.id
            )
            if wandb_url:
                updated = dc.replace(_job, wandb_url=wandb_url)
                jobs_list[idx] = updated
                ctx.logger.info(f"Associated job {_job.id} with W&B run: {wandb_url}")

                if ctx.config.webhooks_enabled:
                    await webhooks.update_job_wandb(ctx.logger, job=updated)

    ctx.state.jobs = tuple(jobs_list)


async def start_queued_jobs(ctx: context.NexusServiceContext) -> None:
    available_gpus = [
        g for g in gpu.get_gpus(ctx.logger, state=ctx.state, mock_gpus=ctx.config.mock_gpus) if g.is_available
    ]
    queued = [_job for _job in ctx.state.jobs if _job.status == "queued"]

    if not queued:
        ctx.logger.debug("No jobs in queue")
        return

    if not available_gpus:
        running_count = len([job for job in ctx.state.jobs if job.status == "running"])
        ctx.logger.debug(f"No available GPUs. {running_count} jobs running")
        return

    jobs_list = list(ctx.state.jobs)
    for _gpu in available_gpus:
        if not queued:
            break

        _job = queued.pop(0)
        jobs_dir = (
            config.get_jobs_dir(ctx.config.service_dir)
            if not ctx.config.persist_to_disk
            else pl.Path(tempfile.mkdtemp())
        )

        started = await job.async_start_job(
            ctx.logger, job=_job, gpu_index=_gpu.index, jobs_dir=jobs_dir, _env=ctx.env.model_dump()
        )
        for i, j in enumerate(jobs_list):
            if j.id == _job.id:
                jobs_list[i] = started
                break

        ctx.logger.info(format.format_job_action(started, action="started"))

        if ctx.config.webhooks_enabled:
            await webhooks.notify_job_started(ctx.logger, job=started)

    ctx.state.jobs = tuple(jobs_list)
    ctx.logger.info(f"Started jobs on available GPUs; remaining queued jobs: {len(queued)}")


async def scheduler_loop(ctx: context.NexusServiceContext):
    while True:
        try:
            await update_running_jobs(ctx=ctx)
            await update_wandb_urls(ctx=ctx)
            await start_queued_jobs(ctx=ctx)

            if ctx.config.persist_to_disk:
                state.save_state(ctx.state, state_path=config.get_state_path(ctx.config.service_dir))

        except Exception:
            ctx.logger.exception("Scheduler encountered an error:")
        await asyncio.sleep(ctx.config.refresh_rate)
