import subprocess
import warnings

from nexus.service.models import GpuInfo, ServiceState

# Mock GPUs for testing/development
MOCK_GPUS = [
    GpuInfo(
        index=0,
        name="Mock GPU 0",
        memory_total=8192,
        memory_used=2048,
        is_blacklisted=False,
        running_job_id=None,
    ),
    GpuInfo(
        index=1,
        name="Mock GPU 1",
        memory_total=16384,
        memory_used=4096,
        is_blacklisted=False,
        running_job_id=None,
    ),
]


def get_gpus() -> list[GpuInfo]:
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
                running_job_id=None,  # Updated based on running jobs
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as e:
            warnings.warn(f"Error parsing GPU info: {e}")
            continue

    return gpus if gpus else MOCK_GPUS


def get_available_gpus(state: ServiceState) -> list[GpuInfo]:
    gpus = get_gpus()
    running_jobs = {j.gpu_index: j.id for j in state.jobs if j.status == "running"}

    # Filter available GPUs
    available_gpus = [
        g
        for g in gpus
        if not g.is_blacklisted and g.index not in running_jobs and g.memory_used == 0
    ]
    return available_gpus
