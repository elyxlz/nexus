import asyncio
import dataclasses as dc
import datetime as dt

from nexus.service import config, format, git, gpu, job, logger, models, state, wandb_finder, webhooks


async def update_running_jobs(_state: models.NexusServiceState, _config: config.NexusServiceConfig) -> None:
    jobs_list = list(_state.jobs)
    for idx, _job in enumerate(jobs_list):
        if _job.status != "running":
            continue

        if _job.marked_for_kill and job.is_job_session_running(_job.id):
            job.kill_job_session(_job.id)
            updated = job.end_job(_job, jobs_dir=config.get_jobs_dir(_config.service_dir), killed=True)
            git.cleanup_repo(config.get_jobs_dir(_config.service_dir), job_id=_job.id)

        elif not job.is_job_session_running(_job.id):
            updated = job.end_job(_job, jobs_dir=config.get_jobs_dir(_config.service_dir), killed=False)
            git.cleanup_repo(config.get_jobs_dir(_config.service_dir), job_id=_job.id)

        else:
            continue

        if updated.status != "running":
            action = "completed" if updated.status == "completed" else "failed"

            if action == "completed":
                logger.info(format.format_job_action(updated, action=action))
            else:
                logger.error(format.format_job_action(updated, action=action))

            running_jobs = [j for j in jobs_list if j.status == "running"]

            if not any(job.git_tag == updated.git_tag for job in running_jobs):
                git.cleanup_git_tag(_job.git_tag, git_repo_url=_job.git_repo_url)

            if _config.webhooks_enabled:
                if action == "completed":
                    await webhooks.notify_job_completed(_job)
                elif action == "failed":
                    job_logs = job.get_job_logs(
                        _job.id, jobs_dir=config.get_jobs_dir(_config.service_dir), last_n_lines=20
                    )
                    await webhooks.notify_job_failed(_job, job_logs=job_logs)

            if action == "failed":
                last_lines = job.get_job_logs(
                    updated.id, jobs_dir=config.get_jobs_dir(_config.service_dir), last_n_lines=20
                )
                if last_lines:
                    logger.error(f"Last 20 lines of job log:\n{''.join(last_lines)}")
        jobs_list[idx] = updated
    _state.jobs = tuple(jobs_list)


async def update_wandb_urls(_state: models.NexusServiceState, _config: config.NexusServiceConfig) -> None:
    jobs_list = list(_state.jobs)
    for idx, _job in enumerate(jobs_list):
        if _job.status == "running" and not _job.wandb_url:
            assert _job.started_at is not None
            runtime = dt.datetime.now().timestamp() - _job.started_at

            if runtime > 720:
                continue

            job_repo_dir = config.get_jobs_dir(_config.service_dir) / _job.id / "repo"
            if not job_repo_dir.exists():
                continue

            wandb_url = wandb_finder.find_wandb_run_by_nexus_id([str(job_repo_dir)], nexus_job_id=_job.id)
            if wandb_url:
                updated = dc.replace(_job, wandb_url=wandb_url)
                jobs_list[idx] = updated
                logger.info(f"Associated job {_job.id} with W&B run: {wandb_url}")

                if _config.webhooks_enabled:
                    await webhooks.update_job_wandb(updated)

    _state.jobs = tuple(jobs_list)


async def clean_old_jobs(_state: models.NexusServiceState, _config: config.NexusServiceConfig) -> None:
    initial = len(_state.jobs)
    active = [job for job in _state.jobs if job.status not in ("completed", "failed")]

    completed_failed = sorted(
        [job for job in _state.jobs if job.status in ("completed", "failed")],
        key=lambda j: j.completed_at or 0,
        reverse=True,
    )[: _config.history_limit]
    _state.jobs = tuple(active + completed_failed)

    if len(_state.jobs) < initial:
        logger.debug(f"Cleaned {initial - len(_state.jobs)} old completed jobs")


async def start_queued_jobs(_state: models.NexusServiceState, _config: config.NexusServiceConfig) -> None:
    available_gpus = [g for g in gpu.get_gpus(_state, mock_gpus=_config.mock_gpus) if g.is_available]
    queued = [_job for _job in _state.jobs if _job.status == "queued"]

    if not queued:
        logger.debug("No jobs in queue")
        return

    if not available_gpus:
        running_count = len([job for job in _state.jobs if job.status == "running"])
        logger.debug(f"No available GPUs. {running_count} jobs running")
        return

    jobs_list = list(_state.jobs)
    for _gpu in available_gpus:
        if not queued:
            break

        _job = queued.pop(0)
        started = job.start_job(
            _job,
            gpu_index=_gpu.index,
            jobs_dir=config.get_jobs_dir(_config.service_dir),
            env_file=config.get_jobs_dir(_config.service_dir).parent / ".env",
        )
        for i, j in enumerate(jobs_list):
            if j.id == _job.id:
                jobs_list[i] = started
                break

        logger.info(format.format_job_action(started, action="started"))
        if _config.webhooks_enabled:
            await webhooks.notify_job_started(started)

    _state.jobs = tuple(jobs_list)
    logger.info(f"Started jobs on available GPUs; remaining queued jobs: {len(queued)}")


async def scheduler_loop(_state: models.NexusServiceState, _config: config.NexusServiceConfig):
    while True:
        try:
            await update_running_jobs(_state, _config=_config)
            await update_wandb_urls(_state, _config=_config)
            await clean_old_jobs(_state, _config=_config)
            await start_queued_jobs(_state, _config=_config)

            if _config.persist_to_disk:
                state.save_state(_state, state_path=config.get_state_path(_config.service_dir))

        except Exception:
            logger.exception("Scheduler encountered an error:")
        await asyncio.sleep(_config.refresh_rate)
