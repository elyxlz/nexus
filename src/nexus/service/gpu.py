import subprocess
import warnings

from nexus.service.models import GpuInfo, ServiceState


def get_gpu_processes() -> dict[int, list[int]]:
    """
    Query nvidia-smi for processes using GPUs.
    Returns a dictionary mapping GPU indices to lists of PIDs.
    """
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "pmon", "-c", "1"],
            text=True,
        )
        # Skip header lines
        lines = output.strip().split("\n")[2:]

        gpu_processes: dict[int, list[int]] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                parts = line.split()
                if len(parts) >= 2:
                    gpu_idx = int(parts[0])
                    pid = int(parts[1])
                    if pid != -1:  # -1 indicates no process
                        if gpu_idx not in gpu_processes:
                            gpu_processes[gpu_idx] = []
                        gpu_processes[gpu_idx].append(pid)
            except (ValueError, IndexError) as e:
                warnings.warn(f"Error parsing process info: {e}")
                continue

        return gpu_processes
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        warnings.warn(f"nvidia-smi pmon failed: {e}")
        return {}


def get_gpus(state: ServiceState) -> list[GpuInfo]:
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

        # Get process information for each GPU
        gpu_processes = get_gpu_processes()

        gpus = []
        for line in output.strip().split("\n"):
            try:
                # Parse GPU information
                index, name, total, used = [x.strip() for x in line.split(",")]
                index = int(index)

                # Create GpuInfo object with process count from gpu_processes
                gpu = GpuInfo(
                    index=index,
                    name=name,
                    memory_total=int(float(total)),
                    memory_used=int(float(used)),
                    process_count=len(gpu_processes.get(index, [])),  # Number of processes on this GPU
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


def get_available_gpus(state: ServiceState) -> list[GpuInfo]:
    """
    Get available GPUs based on:
    1. Not blacklisted
    2. Not assigned to a running job in our service
    3. No processes currently using the GPU
    """
    gpus = get_gpus(state)
    gpu_processes = get_gpu_processes()  # Retrieve GPU processes to filter

    # Filter available GPUs based on the process list and blacklist
    available_gpus = [
        g
        for g in gpus
        if (
            not g.is_blacklisted  # Not blacklisted
            and g.running_job_id is None  # Not assigned to a running job in our service
            and g.index not in gpu_processes  # No processes using this GPU
        )
    ]
    return available_gpus


# Mock GPUs for testing/development
def get_mock_gpus(state: ServiceState) -> list[GpuInfo]:
    """Generate mock GPUs for testing purposes."""
    return [
        GpuInfo(
            index=0,
            name="Mock GPU 0",
            memory_total=8192,
            memory_used=1,
            process_count=0,
            is_blacklisted=0 in state.blacklisted_gpus,
            running_job_id=None,
        ),
        GpuInfo(
            index=1,
            name="Mock GPU 1",
            memory_total=16384,
            memory_used=1,
            process_count=0,
            is_blacklisted=1 in state.blacklisted_gpus,
            running_job_id=None,
        ),
    ]
