import os
import subprocess
import time

import termcolor as tc

from nexus import models, utils


def create_job(command: str, config: models.Config) -> models.Job:
    job_id = utils.generate_job_id()
    log_dir = config.log_dir / "jobs" / job_id

    return models.Job(
        id=job_id,
        command=command.strip(),
        status=models.JobStatus.QUEUED,
        created_at=time.time(),
        started_at=None,
        completed_at=None,
        gpu_index=None,
        screen_session=None,
        env_vars=[],
        exit_code=None,
        error_message=None,
        log_dir=log_dir,
    )


def start_job(
    job: models.Job, gpu_index: int, state: models.NexusState, config: models.Config
) -> None:
    session_name = f"nexus_job_{job.id}"
    assert job.log_dir is not None

    job.log_dir.mkdir(parents=True, exist_ok=True)

    # Prepare environment variables
    env = os.environ.copy()
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": str(gpu_index),
            "NEXUS_JOB_ID": job.id,
            "NEXUS_GPU_ID": str(gpu_index),
            "NEXUS_START_TIME": str(time.time()),
        }
    )

    # Remove problematic screen variables
    env = {k: v for k, v in env.items() if not k.startswith("SCREEN_")}

    stdout_log = job.log_dir / "stdout.log"
    stderr_log = job.log_dir / "stderr.log"

    script_path = job.log_dir / "run.sh"
    script_content = f"""#!/bin/bash
exec 1> "{stdout_log}" 2> "{stderr_log}"
{job.command}
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    try:
        subprocess.run(
            ["screen", "-dmS", session_name, str(script_path)], env=env, check=True
        )

        job.started_at = time.time()
        job.gpu_index = gpu_index
        job.screen_session = session_name
        job.status = models.JobStatus.RUNNING
        job.env_vars = list(env.items())

        utils.log_service_event(config, f"Job {job.id} started on GPU {gpu_index}")
        utils.save_state(state, config)

    except subprocess.CalledProcessError as e:
        job.status = models.JobStatus.FAILED
        job.error_message = str(e)
        job.completed_at = time.time()
        utils.log_service_event(config, f"Failed to start job {job.id}: {e}")
        utils.save_state(state, config)
        raise


def start_service_in_screen(config: models.Config) -> None:
    session_name = "nexus"

    if not utils.is_screen_session_alive("nexus"):
        service_log = config.log_dir / "service.log"
        command = f"exec 1> {service_log} 2>&1; python {__file__} service"
        breakpoint()

        try:
            subprocess.run(
                ["screen", "-dmS", session_name, "bash", "-c", command], check=True
            )
            time.sleep(1)
            assert utils.is_screen_session_alive(session_name)
            print(tc.colored("Nexus service started", "green"))
            utils.log_service_event(config, "Nexus service started")
        except subprocess.CalledProcessError as e:
            print(tc.colored(f"Failed to start service: {e}", "red"))
    else:
        print(tc.colored("Nexus service is already running", "yellow"))


def nexus_service(config: models.Config) -> None:
    breakpoint()
    utils.log_service_event(config, "Service starting")
    state = utils.load_state(config=config)

    while True:
        try:
            gpus = utils.get_gpu_info(config, state)

            # Update job statuses
            for job in state.jobs:
                if job.status == models.JobStatus.RUNNING:
                    if not utils.is_screen_session_alive(job.screen_session):
                        job.status = models.JobStatus.COMPLETED
                        job.completed_at = time.time()
                        utils.log_service_event(config, f"Job {job.id} completed")
                        utils.save_state(state, config)

            # Find available non-blacklisted GPUs
            available_gpus = [
                g
                for g in gpus
                if not g.is_blacklisted
                and not any(
                    j.status == models.JobStatus.RUNNING and j.gpu_index == g.index
                    for j in state.jobs
                )
            ]

            # Start new jobs if queue is not paused
            if not state.is_paused:
                for gpu in available_gpus:
                    queued_jobs = [
                        j for j in state.jobs if j.status == models.JobStatus.QUEUED
                    ]
                    if queued_jobs:
                        job = queued_jobs[0]
                        try:
                            start_job(job, gpu.index, state, config)
                        except Exception as e:
                            utils.log_service_event(
                                config, f"Failed to start job {job.id}: {e}"
                            )
                            job.status = models.JobStatus.FAILED
                            job.completed_at = time.time()
                            job.error_message = str(e)
                            utils.save_state(state, config)

            time.sleep(config.refresh_rate)

        except Exception as e:
            utils.log_service_event(config, f"Service error: {e}")
            time.sleep(config.refresh_rate)
