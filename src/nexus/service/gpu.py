import subprocess

from nexus.service.logger import logger
from nexus.service.models import GpuInfo, ServiceState


def get_gpus(state: ServiceState) -> list[GpuInfo]:
    """
    Query nvidia-smi for GPU information including processes
    Returns list of GpuInfo objects with complete GPU state
    """
    try:
        # Single nvidia-smi call to get both GPU stats and processes
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,compute_process_pids",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )

        running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running"}
        gpus = []

        for line in output.strip().split("\n"):
            try:
                # Parse GPU info
                parts = [x.strip() for x in line.split(",")]
                if len(parts) < 5:
                    logger.error(f"Unexpected nvidia-smi output format: {line}")
                    continue

                index = int(parts[0])
                name = parts[1]
                total_memory = int(float(parts[2]))
                used_memory = int(float(parts[3]))

                # Parse process PIDs
                pids = []
                if parts[4]:  # If there are processes
                    try:
                        pids = [int(pid) for pid in parts[4].split()]
                    except ValueError as e:
                        logger.error(f"Error parsing process PIDs for GPU {index}: {e}")

                gpu = GpuInfo(
                    index=index,
                    name=name,
                    memory_total=total_memory,
                    memory_used=used_memory,
                    process_count=len(pids),
                    is_blacklisted=index in state.blacklisted_gpus,
                    running_job_id=running_jobs.get(index),
                )
                gpus.append(gpu)

            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing GPU info: {e}")
                continue

        return gpus if gpus else get_mock_gpus(state)

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.debug(f"nvidia-smi not available or failed: {e}. Using mock GPU information.")
        return get_mock_gpus(state)


def get_mock_gpus(state: ServiceState) -> list[GpuInfo]:
    """
    Create mock GPUs reflecting the current service state
    """
    running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running"}

    return [
        GpuInfo(
            index=0,
            name="Mock GPU 0",
            memory_total=8192,
            memory_used=1,
            process_count=0,
            is_blacklisted=0 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(0),
        ),
        GpuInfo(
            index=1,
            name="Mock GPU 1",
            memory_total=16384,
            memory_used=1,
            process_count=0,
            is_blacklisted=1 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(1),
        ),
    ]


def get_available_gpus(state: ServiceState) -> list[GpuInfo]:
    """
    Get available GPUs based on:
    1. Not blacklisted
    2. Not assigned to a running job in our service
    3. No processes currently using the GPU
    """
    gpus = get_gpus(state)
    return [g for g in gpus if (not g.is_blacklisted and g.running_job_id is None and g.process_count == 0)]
