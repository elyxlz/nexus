import dataclasses as dc
import subprocess
import typing as tp

from nexus.server.core import exceptions as exc
from nexus.server.core import logger, schemas

__all__ = ["GpuInfo", "get_gpus", "is_gpu_available"]


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
def _run_command(_logger: logger.NexusServerLogger, command: list[str], timeout: int = 5) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def _fetch_gpu_processes(_logger: logger.NexusServerLogger) -> GpuProcesses:
    _logger.debug("Executing nvidia-smi pmon command")
    output = _run_command(_logger, ["nvidia-smi", "pmon", "-c", "1"])

    gpu_processes: GpuProcesses = {}
    for line in output.strip().split("\n")[2:]:
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) > 1 and parts[1].strip() != "-":
            gpu_idx = int(parts[0])
            gpu_processes[gpu_idx] = gpu_processes.get(gpu_idx, 0) + 1
            _logger.debug(f"GPU {gpu_idx}: process count incremented to {gpu_processes[gpu_idx]}")
    _logger.debug(f"Final GPU process counts: {gpu_processes}")
    return gpu_processes


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
def _parse_gpu_line(
    _logger: logger.NexusServerLogger, line: str, gpu_processes: dict, blacklisted_gpus: set, running_jobs_idxs: dict
) -> GpuInfo:
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


def _get_nvidia_smi_output(_logger: logger.NexusServerLogger) -> str:
    _logger.debug("Executing nvidia-smi command for GPU stats")
    output = _run_command(
        _logger, ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"]
    )

    if not output.strip():
        error = exc.GPUError(
            message="nvidia-smi returned no output. Ensure that nvidia-smi is installed and GPUs are available."
        )
        _logger.debug(f"GPU error code: {error.code}")
        raise error

    return output


@exc.handle_exception(ValueError, message="Error processing GPU line", reraise=False)
def _process_gpu_line(
    line: str,
    gpu_processes: GpuProcesses,
    blacklisted_set: set[int],
    running_jobs_idxs: dict[int, str],
    _logger: logger.NexusServerLogger,
) -> GpuInfo:
    return _parse_gpu_line(
        _logger,
        line=line,
        gpu_processes=gpu_processes,
        blacklisted_gpus=blacklisted_set,
        running_jobs_idxs=running_jobs_idxs,
    )


def _get_mock_gpus(
    _logger: logger.NexusServerLogger, running_jobs: list[schemas.Job], blacklisted_gpus: list[int]
) -> list[GpuInfo]:
    _logger.debug("Generating mock GPUs")
    running_jobs_idxs = {tp.cast(int, j.gpu_idx): j.id for j in running_jobs}
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
        _logger.debug(f"Mock GPU {gpu.index} availability: {is_gpu_available(gpu)}")

    _logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus


####################


def is_gpu_available(gpu_info: GpuInfo) -> bool:
    return not gpu_info.is_blacklisted and gpu_info.running_job_id is None and gpu_info.process_count == 0


def get_gpus(
    _logger: logger.NexusServerLogger, running_jobs: list[schemas.Job], blacklisted_gpus: list[int], mock_gpus: bool
) -> list[GpuInfo]:
    if mock_gpus:
        return _get_mock_gpus(_logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted_gpus)

    output = _get_nvidia_smi_output(_logger)
    gpu_processes = _fetch_gpu_processes(_logger)
    running_jobs_idxs = {tp.cast(int, j.gpu_idx): j.id for j in running_jobs}
    blacklisted_set = set(blacklisted_gpus)
    gpus: list[GpuInfo] = []

    for line in output.strip().split("\n"):
        gpu = _process_gpu_line(line, gpu_processes, blacklisted_set, running_jobs_idxs, _logger)
        if gpu:
            gpus.append(gpu)

    _logger.debug(f"Total GPUs found: {len(gpus)}")
    if not gpus:
        _logger.warning("No GPUs detected on the system")

    return gpus
