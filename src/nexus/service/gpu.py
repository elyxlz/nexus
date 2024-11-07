import subprocess
import warnings

from nexus.service import models
from nexus.service.logger import logger


def is_gpu_available(gpu_info: models.GpuInfo) -> bool:
    return not gpu_info.is_blacklisted and gpu_info.running_job_id is None and gpu_info.process_count == 0


def get_gpu_processes() -> dict[int, int]:
    """Query nvidia-smi pmon for process information per GPU.
    Returns a dictionary mapping GPU indices to their process counts."""
    try:
        logger.debug("Executing nvidia-smi pmon command")
        output = subprocess.check_output(
            ["nvidia-smi", "pmon", "-c", "1"],
            text=True,
        )

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


def get_gpus(state: models.ServiceState) -> list[models.GpuInfo]:
    """Query nvidia-smi for GPU information and map to process information."""
    try:
        logger.debug("Executing nvidia-smi command for GPU stats")
        # Get GPU stats
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )

        # Get process counts for each GPU
        gpu_processes = get_gpu_processes()

        # Get process information for each GPU
        gpus = []
        for line in output.strip().split("\n"):
            try:
                # Parse GPU information
                index, name, total, used = [x.strip() for x in line.split(",")]
                index = int(index)
                logger.debug(f"Processing GPU {index}: {name}")
                # Create models.GpuInfo object with process count from gpu_processes
                gpu = models.GpuInfo(
                    index=index,
                    name=name,
                    memory_total=int(float(total)),
                    memory_used=int(float(used)),
                    process_count=gpu_processes.get(index, 0),  # Get process count, default to 0
                    is_blacklisted=index in state.blacklisted_gpus,
                    running_job_id={j.gpu_index: j.id for j in state.jobs if j.status == "running"}.get(index),
                    is_available=False,
                )
                gpu.is_available = is_gpu_available(gpu)
                logger.debug(f"GPU {index} availability: {gpu.is_available}")

                gpus.append(gpu)
            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing GPU info: {e}")
                warnings.warn(f"Error parsing GPU info: {e}")
                continue
        logger.debug(f"Total GPUs found: {len(gpus)}")
        return gpus if gpus else get_mock_gpus(state)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Using mock gpus: nvidia-smi not available or failed: {e}")
        return get_mock_gpus(state)


# Mock GPUs for testing/development
def get_mock_gpus(state: models.ServiceState) -> list[models.GpuInfo]:
    """Generate mock GPUs for testing purposes."""
    logger.debug("Generating mock GPUs")
    running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running"}
    mock_gpus = [
        models.GpuInfo(
            index=0,
            name="Mock GPU 0",
            memory_total=8192,
            memory_used=1,
            process_count=0,
            is_blacklisted=0 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(0),
            is_available=False,
        ),
        models.GpuInfo(
            index=1,
            name="Mock GPU 1",
            memory_total=16384,
            memory_used=1,
            process_count=0,
            is_blacklisted=1 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(1),
            is_available=False,
        ),
    ]

    for gpu in mock_gpus:
        gpu.is_available = is_gpu_available(gpu)
        logger.debug(f"Mock GPU {gpu.index} availability: {gpu.is_available}")

    logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus
