import subprocess
import typing

from nexus.service.core import logger, models

GpuProcesses = dict[int, int]


def is_gpu_available(gpu_info: models.GpuInfo) -> bool:
    """Determine GPU availability based on blacklist status, job assignment, and process count."""
    return not gpu_info.is_blacklisted and gpu_info.running_job_id is None and gpu_info.process_count == 0


def run_command(command: list[str], timeout: int = 5) -> str:
    """
    Execute an external command with a timeout and return its output.
    Errors will naturally propagate.
    """
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def fetch_gpu_processes(logger: logger.NexusServiceLogger) -> GpuProcesses:
    """
    Query nvidia-smi pmon for process information per GPU.
    Returns a dictionary mapping GPU indices to their process counts.
    """
    logger.debug("Executing nvidia-smi pmon command")
    output = run_command(["nvidia-smi", "pmon", "-c", "1"])

    gpu_processes: GpuProcesses = {}
    # Assume the first two lines are headers.
    for line in output.strip().split("\n")[2:]:
        if not line.strip():
            continue

        parts = line.split()
        # Only count lines that represent a process (lines with '-' indicate no process)
        if len(parts) > 1 and parts[1].strip() != "-":
            gpu_index = int(parts[0])
            gpu_processes[gpu_index] = gpu_processes.get(gpu_index, 0) + 1
            logger.debug(f"GPU {gpu_index}: process count incremented to {gpu_processes[gpu_index]}")
    logger.debug(f"Final GPU process counts: {gpu_processes}")
    return gpu_processes


def create_gpu_info(
    index: int,
    name: str,
    total_memory: int,
    used_memory: int,
    process_count: int,
    blacklisted_gpus: set[int],
    running_jobs: dict[int, str],
) -> models.GpuInfo:
    gpu = models.GpuInfo(
        index=index,
        name=name,
        memory_total=total_memory,
        memory_used=used_memory,
        process_count=process_count,
        is_blacklisted=index in blacklisted_gpus,
        running_job_id=running_jobs.get(index),
    )
    return gpu


def get_gpus(
    logger: logger.NexusServiceLogger, running_jobs: list[models.Job], blacklisted_gpus: list[int], mock_gpus: bool
) -> list[models.GpuInfo]:
    """
    Retrieve GPU information using nvidia-smi, or return mock data if requested.
    """
    if mock_gpus:
        logger.info("MOCK_GPUS parameter is True. Returning mock GPU information.")
        return get_mock_gpus(logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted_gpus)

    logger.debug("Executing nvidia-smi command for GPU stats")
    output = run_command(
        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"]
    )
    if not output.strip():
        raise RuntimeError("nvidia-smi returned no output. Ensure that nvidia-smi is installed and GPUs are available.")

    gpu_processes = fetch_gpu_processes(logger)
    running_jobs_idxs = {typing.cast(int, j.gpu_index): j.id for j in running_jobs}
    gpus: list[models.GpuInfo] = []

    for line in output.strip().split("\n"):
        index, name, total, used = (x.strip() for x in line.split(","))
        gpu = create_gpu_info(
            int(index),
            name=name,
            total_memory=int(float(total)),
            used_memory=int(float(used)),
            process_count=gpu_processes.get(int(index), 0),
            blacklisted_gpus=set(blacklisted_gpus),
            running_jobs=running_jobs_idxs,
        )
        gpus.append(gpu)

    logger.debug(f"Total GPUs found: {len(gpus)}")
    return gpus


def get_mock_gpus(
    logger: logger.NexusServiceLogger, running_jobs: list[models.Job], blacklisted_gpus: list[int]
) -> list[models.GpuInfo]:
    """
    Generate mock GPUs for testing purposes.
    """
    logger.debug("Generating mock GPUs")
    running_jobs_idxs = {typing.cast(int, j.gpu_index): j.id for j in running_jobs}
    blacklisted_gpus_set = set(blacklisted_gpus)

    mock_gpu_configs = [
        (0, "Mock GPU 0", 8192, 1),
        (1, "Mock GPU 1", 16384, 1),
    ]

    mock_gpus = [
        create_gpu_info(
            index,
            name=name,
            total_memory=total,
            used_memory=used,
            process_count=0,
            blacklisted_gpus=blacklisted_gpus_set,
            running_jobs=running_jobs_idxs,
        )
        for index, name, total, used in mock_gpu_configs
    ]

    for gpu in mock_gpus:
        logger.debug(f"Mock GPU {gpu.index} availability: {is_gpu_available(gpu)}")

    logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus
