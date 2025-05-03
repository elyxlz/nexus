import dataclasses as dc
import subprocess
import time

from nexus.server.core import exceptions as exc
from nexus.server.core import schemas
from nexus.server.utils import logger

__all__ = ["GpuInfo", "get_gpus", "is_gpu_available"]

_nvidia_smi_cache = {"timestamp": 0.0, "output": "", "processes": {}}

CACHE_TTL = 5


@dc.dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    memory_total: int
    memory_used: int
    process_count: int
    is_blacklisted: bool
    running_job_id: str | None


GpuProcesses = dict[int, int]


@exc.handle_exception(subprocess.TimeoutExpired, exc.GPUError, message="Command timed out")
@exc.handle_exception(subprocess.CalledProcessError, exc.GPUError, message="Command failed with error")
@exc.handle_exception(Exception, exc.GPUError, message="Error executing command")
def _run_command(command: list[str], timeout: int = 5) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def _create_gpu_info(
    index: int,
    name: str,
    total_memory: int,
    used_memory: int,
    process_count: int,
    blacklisted_gpus: set[int],
    running_jobs: dict[int, str],
) -> GpuInfo:
    gpu = GpuInfo(
        index=index,
        name=name,
        memory_total=total_memory,
        memory_used=used_memory,
        process_count=process_count,
        is_blacklisted=index in blacklisted_gpus,
        running_job_id=running_jobs.get(index),
    )
    return gpu


@exc.handle_exception(ValueError, exc.GPUError, message="Error parsing GPU info line")
def _parse_gpu_line(line: str, gpu_processes: dict, blacklisted_gpus: set, running_jobs_idxs: dict) -> GpuInfo:
    index, name, total, used = (x.strip() for x in line.split(","))
    return _create_gpu_info(
        int(index),
        name=name,
        total_memory=int(float(total)),
        used_memory=int(float(used)),
        process_count=gpu_processes.get(int(index), 0),
        blacklisted_gpus=blacklisted_gpus,
        running_jobs=running_jobs_idxs,
    )


def _get_mock_gpus(running_jobs: list[schemas.Job], blacklisted_gpus: list[int]) -> list[GpuInfo]:
    logger.debug("Generating mock GPUs")
    running_jobs_idxs = {gpu_idx: j.id for j in running_jobs for gpu_idx in j.gpu_idxs}
    blacklisted_gpus_set = set(blacklisted_gpus)

    mock_gpu_configs = [
        (0, "Mock GPU 0", 8192, 1),
        (1, "Mock GPU 1", 16384, 1),
    ]

    mock_gpus = [
        _create_gpu_info(
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
        basic_availability = not gpu.is_blacklisted and gpu.running_job_id is None and gpu.process_count == 0
        logger.debug(f"Mock GPU {gpu.index} availability: {basic_availability}")

    logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus


def is_gpu_available(gpu_info: GpuInfo, ignore_blacklist: bool = False, required: list[int] | None = None) -> bool:
    return (
        (ignore_blacklist or not gpu_info.is_blacklisted)
        and gpu_info.running_job_id is None
        and gpu_info.process_count == 0
        and (not required or not len(required) or gpu_info.index in required)
    )


@exc.handle_exception(ValueError, exc.GPUError, message="Error parsing GPU pmon line", reraise=False)
def parse_pmon_line(line: str) -> int | None:
    if not line.strip() or line.lstrip().startswith("#"):
        raise ValueError("Line is empty or a header/comment")
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("Line does not contain enough fields")

    # Check if the line indicates no process is running (contains "-" markers)
    if parts[1] == "-" and parts[2] == "-":
        return None

    return int(parts[0])


def _get_gpu_info() -> tuple[str, dict[int, int]]:
    current_time = time.time()
    cache_age = current_time - _nvidia_smi_cache["timestamp"]

    if cache_age < CACHE_TTL and _nvidia_smi_cache["output"] and _nvidia_smi_cache["processes"]:
        logger.debug("Using cached GPU information")
        return _nvidia_smi_cache["output"], _nvidia_smi_cache["processes"]

    logger.debug("Refreshing GPU cache")
    output = _run_command(
        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"]
    )

    if not output.strip():
        raise exc.GPUError(
            message="nvidia-smi returned no output. Ensure that nvidia-smi is installed and GPUs are available."
        )

    pmon_output = _run_command(["nvidia-smi", "pmon", "-c", "1", "-s", "m"])

    gpu_processes: dict[int, int] = {}
    for line in pmon_output.strip().split("\n")[2:]:
        gpu_idx = parse_pmon_line(line)
        if gpu_idx is None:
            continue
        gpu_processes[gpu_idx] = gpu_processes.get(gpu_idx, 0) + 1

    _nvidia_smi_cache["timestamp"] = current_time
    _nvidia_smi_cache["output"] = output
    _nvidia_smi_cache["processes"] = gpu_processes

    return output, gpu_processes


def get_gpus(running_jobs: list[schemas.Job], blacklisted_gpus: list[int], mock_gpus: bool) -> list[GpuInfo]:
    if mock_gpus:
        return _get_mock_gpus(running_jobs, blacklisted_gpus=blacklisted_gpus)

    output, gpu_processes = _get_gpu_info()
    running_jobs_idxs = {gpu_idx: j.id for j in running_jobs for gpu_idx in j.gpu_idxs}
    blacklisted_set = set(blacklisted_gpus)
    gpus: list[GpuInfo] = []

    for line in output.strip().split("\n"):
        gpu = _parse_gpu_line(
            line,
            gpu_processes=gpu_processes,
            blacklisted_gpus=blacklisted_set,
            running_jobs_idxs=running_jobs_idxs,
        )
        if gpu:
            gpus.append(gpu)

    logger.debug(f"Total GPUs found: {len(gpus)}")
    if not gpus:
        logger.warning("No GPUs detected on the system")

    return gpus
