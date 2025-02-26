import subprocess
import typing

from nexus.service.core import exceptions as exc
from nexus.service.core import logger, models

__all__ = ["get_gpus", "is_gpu_available"]

GpuProcesses = dict[int, int]


def is_gpu_available(gpu_info: models.GpuInfo) -> bool:
    """Determine GPU availability based on blacklist status, job assignment, and process count."""
    return not gpu_info.is_blacklisted and gpu_info.running_job_id is None and gpu_info.process_count == 0


@exc.handle_exception(subprocess.TimeoutExpired, exc.GPUError, message="Command timed out")
@exc.handle_exception(subprocess.CalledProcessError, exc.GPUError, message="Command failed with error")
@exc.handle_exception(Exception, exc.GPUError, message="Error executing command")
def run_command(_logger: logger.NexusServiceLogger, command: list[str], timeout: int = 5) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def fetch_gpu_processes(_logger: logger.NexusServiceLogger) -> GpuProcesses:
    """
    Query nvidia-smi pmon for process information per GPU.
    Returns a dictionary mapping GPU indices to their process counts.
    """
    _logger.debug("Executing nvidia-smi pmon command")
    output = run_command(_logger, ["nvidia-smi", "pmon", "-c", "1"])

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
            _logger.debug(f"GPU {gpu_index}: process count incremented to {gpu_processes[gpu_index]}")
    _logger.debug(f"Final GPU process counts: {gpu_processes}")
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


@exc.handle_exception(ValueError, exc.GPUError, message="Error parsing GPU info line")
def parse_gpu_line(
    _logger: logger.NexusServiceLogger, line: str, gpu_processes: dict, blacklisted_gpus: set, running_jobs_idxs: dict
) -> models.GpuInfo:
    """Helper function to parse a single GPU line from nvidia-smi output."""
    index, name, total, used = (x.strip() for x in line.split(","))
    return create_gpu_info(
        int(index),
        name=name,
        total_memory=int(float(total)),
        used_memory=int(float(used)),
        process_count=gpu_processes.get(int(index), 0),
        blacklisted_gpus=blacklisted_gpus,
        running_jobs=running_jobs_idxs,
    )


def _get_nvidia_smi_output(_logger: logger.NexusServiceLogger) -> str:
    """Get raw output from nvidia-smi command."""
    _logger.debug("Executing nvidia-smi command for GPU stats")
    output = run_command(
        _logger, ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"]
    )

    # Validate output
    if not output.strip():
        # ERROR_CODE is now a class variable, not a parameter
        error = exc.GPUError(
            message="nvidia-smi returned no output. Ensure that nvidia-smi is installed and GPUs are available."
        )
        # The error code is set automatically based on the class
        _logger.debug(f"GPU error code: {error.code}")
        raise error

    return output


@exc.handle_exception(ValueError, message="Error processing GPU line", reraise=False)
def _process_gpu_line(
    line: str,
    gpu_processes: GpuProcesses,
    blacklisted_set: set[int],
    running_jobs_idxs: dict[int, str],
    _logger: logger.NexusServiceLogger,
) -> models.GpuInfo:
    """Process a single line of GPU data."""
    return parse_gpu_line(
        _logger=_logger,
        line=line,
        gpu_processes=gpu_processes,
        blacklisted_gpus=blacklisted_set,
        running_jobs_idxs=running_jobs_idxs,
    )


def get_gpus(
    _logger: logger.NexusServiceLogger, running_jobs: list[models.Job], blacklisted_gpus: list[int], mock_gpus: bool
) -> list[models.GpuInfo]:
    """
    Retrieve GPU information using nvidia-smi, or return mock data if requested.
    """
    # Early return for mock mode
    if mock_gpus:
        _logger.debug("MOCK_GPUS parameter is True. Returning mock GPU information.")
        return get_mock_gpus(_logger, running_jobs=running_jobs, blacklisted_gpus=blacklisted_gpus)

    # Get GPU data and set up data structures
    output = _get_nvidia_smi_output(_logger)
    gpu_processes = fetch_gpu_processes(_logger)
    running_jobs_idxs = {typing.cast(int, j.gpu_index): j.id for j in running_jobs}
    blacklisted_set = set(blacklisted_gpus)
    gpus: list[models.GpuInfo] = []

    # Process each GPU line
    for line in output.strip().split("\n"):
        gpu = _process_gpu_line(line, gpu_processes, blacklisted_set, running_jobs_idxs, _logger)
        if gpu:
            gpus.append(gpu)

    # Log results
    _logger.debug(f"Total GPUs found: {len(gpus)}")
    if not gpus:
        _logger.warning("No GPUs detected on the system")

    return gpus


def get_mock_gpus(
    _logger: logger.NexusServiceLogger, running_jobs: list[models.Job], blacklisted_gpus: list[int]
) -> list[models.GpuInfo]:
    """
    Generate mock GPUs for testing purposes.
    """
    _logger.debug("Generating mock GPUs")
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
        _logger.debug(f"Mock GPU {gpu.index} availability: {is_gpu_available(gpu)}")

    _logger.debug(f"Total mock GPUs generated: {len(mock_gpus)}")
    return mock_gpus
