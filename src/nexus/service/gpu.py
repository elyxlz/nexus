import subprocess
import warnings
from nexus.service import models


def get_gpu_processes() -> dict[int, int]:
    """Query nvidia-smi pmon for process information per GPU.
    Returns a dictionary mapping GPU indices to their process counts."""
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "pmon", "-c", "1"],
            text=True,
        )

        # Initialize process counts for all GPUs
        gpu_processes = {}

        # Skip header line
        lines = output.strip().split("\n")[1:]

        for line in lines:
            # PMON format: # gpu        pid  type    sm   mem   enc   dec   command
            # We only need the GPU index (first column)
            if not line.strip():
                continue

            parts = line.split()
            if not parts:
                continue

            try:
                gpu_index = int(parts[0])
                gpu_processes[gpu_index] = gpu_processes.get(gpu_index, 0) + 1
            except (ValueError, IndexError):
                continue

        return gpu_processes
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        warnings.warn(f"nvidia-smi pmon failed: {e}", RuntimeWarning)
        return {}


def get_gpus(state: models.ServiceState) -> list[models.GpuInfo]:
    """Query nvidia-smi for GPU information and map to process information."""
    try:
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
                # Create models.GpuInfo object with process count from gpu_processes
                gpu = models.GpuInfo(
                    index=index,
                    name=name,
                    memory_total=int(float(total)),
                    memory_used=int(float(used)),
                    process_count=gpu_processes.get(index, 0),  # Get process count, default to 0
                    is_blacklisted=index in state.blacklisted_gpus,
                    running_job_id={j.gpu_index: j.id for j in state.jobs if j.status == "running"}.get(index),
                )
                gpus.append(gpu)
            except (ValueError, IndexError) as e:
                warnings.warn(f"Error parsing GPU info: {e}")
                continue
        return gpus if gpus else get_mock_gpus(state)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        warnings.warn(
            f"nvidia-smi not available or failed: {e}. Using mock GPU information.",
            RuntimeWarning,
        )
        return get_mock_gpus(state)


def get_available_gpus(state: models.ServiceState) -> list[models.GpuInfo]:
    """
    Get available GPUs based on:
    1. Not blacklisted
    2. Not assigned to a running job in our service
    4. No processes running on the GPU
    """
    gpus = get_gpus(state)
    # Filter available GPUs based on the process list and blacklist
    available_gpus = [
        g
        for g in gpus
        if (
            not g.is_blacklisted  # Not blacklisted
            and g.running_job_id is None  # Not assigned to a running job in our service
            and g.process_count == 0  # No processes using this GPU
        )
    ]
    return available_gpus


# Mock GPUs for testing/development
def get_mock_gpus(state: models.ServiceState) -> list[models.GpuInfo]:
    """Generate mock GPUs for testing purposes."""
    running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running"}
    return [
        models.GpuInfo(
            index=0,
            name="Mock GPU 0",
            memory_total=8192,
            memory_used=1,
            process_count=0,
            is_blacklisted=0 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(0),
        ),
        models.GpuInfo(
            index=1,
            name="Mock GPU 1",
            memory_total=16384,
            memory_used=1,
            process_count=0,
            is_blacklisted=1 in state.blacklisted_gpus,
            running_job_id=running_jobs.get(1),
        ),
    ]
