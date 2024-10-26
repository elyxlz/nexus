import subprocess
import warnings

from nexus.service.models import GpuInfo, Job

# Mock GPUs for testing/development
MOCK_GPUS = [
    GpuInfo(
        index=0,
        name="Mock GPU 0",
        memory_total=8192,
        memory_used=2048,
        is_blacklisted=False,
    ),
    GpuInfo(
        index=1,
        name="Mock GPU 1",
        memory_total=16384,
        memory_used=4096,
        is_blacklisted=False,
    ),
]


def get_gpu_info() -> list[GpuInfo]:
    """Query nvidia-smi for GPU information"""
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
            f"nvidia-smi not available or failed: {e}. Using mock GPU information.",
            RuntimeWarning,
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
                is_blacklisted=False,  # Updated based on service state
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as e:
            warnings.warn(f"Error parsing GPU info: {e}")
            continue

    return gpus if gpus else MOCK_GPUS


def is_gpu_available(gpu: GpuInfo, running_jobs: list[Job]) -> bool:
    """Check if a GPU is available for new jobs"""
    if gpu.is_blacklisted:
        return False

    # Check if any job is using this GPU
    gpu_in_use = any(job.gpu_index == gpu.index for job in running_jobs)

    return not gpu_in_use
