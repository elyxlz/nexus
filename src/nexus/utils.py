import dataclasses as dc
import warnings
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import time

import base58
from termcolor import colored

from nexus.models import Config, GpuInfo, Job, JobStatus, NexusState


def generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:3]
    return base58.b58encode(hash_bytes).decode()


def attach_screen_session(session: str) -> None:
    try:
        subprocess.run(["screen", "-r", session], check=True)
    except subprocess.CalledProcessError as e:
        print(colored(f"Failed to attach to session: {e}", "red"))


def is_screen_session_alive(screen_session: str | None) -> bool:
    if screen_session is None:
        return False

    try:
        output = subprocess.check_output(
            ["screen", "-ls", screen_session], stderr=subprocess.DEVNULL, text=True
        )
        return screen_session in output
    except subprocess.CalledProcessError:
        return False


def get_gpu_info(config: Config, state: NexusState) -> list[GpuInfo]:
    # Mock GPU configuration
    MOCK_GPUS = [
        GpuInfo(0, "Mock GPU 0", 8192, 2048),
        GpuInfo(1, "Mock GPU 1", 16384, 4096),
    ]

    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        warnings.warn(
            "nvidia-smi not available or failed to execute. Using mock GPU information.",
            RuntimeWarning,
        )
        log_service_event(
            config, f"Falling back to mock GPUs due to nvidia-smi failure: {e}"
        )
        return MOCK_GPUS

    gpus = []
    for line in output.strip().split("\n"):
        try:
            index, name, total, used = [x.strip() for x in line.split(",")]
            gpu = GpuInfo(
                index=int(index),
                name=name,
                memory_total=int(float(total)),
                memory_used=int(float(used)),
                is_blacklisted=(int(index) in state.blacklisted_gpus),
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as e:
            log_service_event(config, f"Error parsing GPU info: {e}")
            continue

    return gpus if gpus else MOCK_GPUS


def log_service_event(config: Config, message: str) -> None:
    log_path = config.log_dir / "service.log"
    timestamp = dt.datetime.now(dt.timezone.utc)
    try:
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(colored(f"Failed to write to service log: {e}", "red"))


def load_state(config: Config) -> NexusState:
    state_path = config.log_dir / "state.json"

    try:
        if not state_path.exists():
            return create_default_state()

        with open(state_path) as f:
            data = json.load(f)
            # Convert JobStatus strings back to enum
            for job in data["jobs"]:
                job["status"] = JobStatus(job["status"])
                if job.get("log_dir"):
                    job["log_dir"] = pathlib.Path(job["log_dir"])
            state = NexusState(**data)

            # Convert dict jobs back to Job objects
            state.jobs = [Job(**job) for job in data["jobs"]]

            # Clean up old jobs
            clean_completed_jobs(state, config)
            return state

    except (json.JSONDecodeError, KeyError, TypeError):
        if state_path.exists():
            backup_path = state_path.with_suffix(".json.bak")
            state_path.rename(backup_path)
        return create_default_state()


def save_state(state: NexusState, config: Config) -> None:
    state_path = config.log_dir / "state.json"
    temp_path = state_path.with_suffix(".json.tmp")

    state.last_updated = time.time()

    try:
        state_dict = dc.asdict(state)
        for job in state_dict["jobs"]:
            job["status"] = job["status"].value
            if job.get("log_dir"):
                job["log_dir"] = str(job["log_dir"])

        with open(temp_path, "w") as f:
            json.dump(state_dict, f, indent=2)

        temp_path.replace(state_path)

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def create_default_state() -> NexusState:
    return NexusState(
        jobs=[], blacklisted_gpus=[], is_paused=False, last_updated=time.time()
    )


def clean_completed_jobs(state: NexusState, config: Config) -> None:
    completed = [
        j for j in state.jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    if len(completed) > config.history_limit:
        completed.sort(key=lambda x: x.completed_at or 0, reverse=True)
        keep_jobs = completed[: config.history_limit]
        active_jobs = [
            j for j in state.jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        ]
        state.jobs = active_jobs + keep_jobs
