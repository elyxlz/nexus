import dataclasses as dc
import subprocess
import warnings

from nexus.service import logger, models


def is_gpu_available(logger: logger.NexusServiceLogger, gpu_info: models.GpuInfo) -> bool:
    """Check if a GPU is available for use"""
    return not gpu_info.is_blacklisted and gpu_info.running_job_id is None and gpu_info.process_count == 0


def get_gpu_processes(logger: logger.NexusServiceLogger) -> dict[int, int]:
    """Query nvidia-smi pmon for process information per GPU.
    Returns a dictionary mapping GPU indices to their process counts."""
    try:
        logger.debug("Executing nvidia-smi pmon command")
        output = subprocess.check_output(["nvidia-smi", "pmon", "-c", "1"], text=True)

        # Initialize process counts for all GPUs
        gpu_processes = {}

        # Skip header lines (there are typically 2 header lines)
        lines = output.strip().split("\n")[2:]

        logger.debug(f"Processing {len(lines)} lines of nvidia-smi pmon output")
        for line in lines:
            if not line.strip():
                continue

            parts = line.split()
            if not parts:
                continue

            # Check if the line actually represents a process
            # A line with just "-" indicates no process
            if len(parts) > 1 and parts[1].strip() != "-":
                try:
                    gpu_index = int(parts[0])
                    gpu_processes[gpu_index] = gpu_processes.get(gpu_index, 0) + 1
                    logger.debug(f"GPU {gpu_index}: process count incremented to {gpu_processes[gpu_index]}")
                except (ValueError, IndexError):
                    logger.debug(f"Failed to parse line: {line}")
                    continue

        logger.debug(f"Final GPU process counts: {gpu_processes}")
        return gpu_processes
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"nvidia-smi pmon failed: {e}")
        warnings.warn(f"nvidia-smi pmon failed: {e}", RuntimeWarning)
        return {}


def create_gpu_info(
    logger: logger.NexusServiceLogger,
    index: int,
    name: str,
    total_memory: float | str,
    used_memory: float | str,
    process_count: int,
    blacklisted_gpus: set[int],
    running_jobs: dict[int, str],
) -> models.GpuInfo:
    """Create a GpuInfo instance with computed availability"""
    # Convert memory values to integers
    total_memory_int = int(float(total_memory))
    used_memory_int = int(float(used_memory))

    gpu = models.GpuInfo(
        index=index,
        name=name,
        memory_total=total_memory_int,
        memory_used=used_memory_int,
        process_count=process_count,
        is_blacklisted=index in blacklisted_gpus,
        running_job_id=running_jobs.get(index),
        is_available=False,  # Will be updated below
    )
    return dc.replace(gpu, is_available=is_gpu_available(logger, gpu))


def get_gpus(
    logger: logger.NexusServiceLogger, state: models.NexusServiceState, mock_gpus: bool
) -> list[models.GpuInfo]:
    if mock_gpus:
        logger.info("MOCK_GPUS parameter is True. Returning mock GPU information.")
        return get_mock_gpus(logger, state)

    try:
        logger.debug("Executing nvidia-smi command for GPU stats")
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        if not output.strip():
            raise RuntimeError(
                "nvidia-smi returned no output. Ensure that nvidia-smi is installed and GPUs are available."
            )

        gpu_processes = get_gpu_processes(logger)
        running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running" and j.gpu_index is not None}
        blacklisted_gpus = set(state.blacklisted_gpus)
        gpus = []

        for line in output.strip().split("\n"):
            try:
                index, name, total, used = (x.strip() for x in line.split(","))
                index = int(index)
                gpu = create_gpu_info(
                    logger,
                    index=index,
                    name=name,
                    total_memory=total,
                    used_memory=used,
                    process_count=gpu_processes.get(index, 0),
                    blacklisted_gpus=blacklisted_gpus,
                    running_jobs=running_jobs,
                )
                gpus.append(gpu)
            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing GPU info: {e}")
                continue

        logger.debug(f"Total GPUs found: {len(gpus)}")
        if not gpus:
            raise RuntimeError("No GPUs detected via nvidia-smi.")
        return gpus

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"nvidia-smi not available or failed: {e}")
        raise RuntimeError(f"nvidia-smi not available or failed: {e}")


def get_mock_gpus(logger: logger.NexusServiceLogger, state: models.NexusServiceState) -> list[models.GpuInfo]:
    """Generate mock GPUs for testing purposes."""
    logger.debug("Generating mock GPUs")
    running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running" and j.gpu_index is not None}
    blacklisted_gpus = set(state.blacklisted_gpus)

    mock_gpu_configs = [
        (0, "Mock GPU 0", 8192, 1),
        (1, "Mock GPU 1", 16384, 1),
    ]

    mock_gpus = [
        create_gpu_info(
            logger,
            index=index,
            name=name,
            total_memory=total,
            used_memory=used,
            process_count=0,
            blacklisted_gpus=blacklisted_gpus,
            running_jobs=running_jobs,
        )
        for index, name, total, used in mock_gpu_configs
    ]

    for gpu in mock_gpus:
        logger.debug(f"Mock GPU {gpu.index} availability: {gpu.is_available}")

    logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus
