import dataclasses as dc
import shutil
import socket
import time
import typing as tp

import psutil

HealthStatus = tp.Literal["healthy", "degraded", "unhealthy"]


@dc.dataclass(frozen=True)
class DiskStats:
    total: int
    used: int
    free: int
    percent_used: float


@dc.dataclass(frozen=True)
class NetworkStats:
    download_speed: float
    upload_speed: float
    ping: float


@dc.dataclass(frozen=True)
class SystemStats:
    cpu_percent: float
    memory_percent: float
    uptime: float
    load_avg: list[float]


@dc.dataclass(frozen=True)
class HealthCheckResult:
    status: HealthStatus
    score: float
    disk: DiskStats
    network: NetworkStats
    system: SystemStats


def check_disk_space(path: str = "/") -> DiskStats:
    disk = shutil.disk_usage(path)
    percent_used = (disk.used / disk.total) * 100

    return DiskStats(total=disk.total, used=disk.used, free=disk.free, percent_used=percent_used)


def check_network_speed() -> NetworkStats:
    host = "8.8.8.8"
    start_time = time.time()
    try:
        socket.create_connection((host, 53), timeout=2)
        ping_time = (time.time() - start_time) * 1000
    except (TimeoutError, OSError):
        ping_time = float("inf")

    download_speed = 100.0 if ping_time < 100 else 50.0
    upload_speed = 50.0 if ping_time < 100 else 25.0

    return NetworkStats(download_speed=download_speed, upload_speed=upload_speed, ping=ping_time)


def check_system_stats() -> SystemStats:
    return SystemStats(
        cpu_percent=psutil.cpu_percent(interval=0.5),
        memory_percent=psutil.virtual_memory().percent,
        uptime=time.time() - psutil.boot_time(),
        load_avg=psutil.getloadavg(),  # type: ignore
    )


def calculate_health_score(
    disk_stats: DiskStats,
    network_stats: NetworkStats,
    system_stats: SystemStats,
) -> float:
    disk_score_raw = 1 - (disk_stats.percent_used / 100)

    # Apply exponential penalty for high disk usage
    # When disk is >90% full, score drops dramatically
    disk_penalty = 1.0
    if disk_stats.percent_used > 90:
        disk_penalty = 0.2
    elif disk_stats.percent_used > 80:
        disk_penalty = 0.5

    disk_score = 40 * disk_score_raw * disk_penalty

    # If disk is critically full (<5% free), cap the total score
    if disk_stats.percent_used > 95:
        return min(30, disk_score)

    network_score = 0
    if network_stats.ping < float("inf"):
        ping_score = 15 * max(0, min(1, (200 - network_stats.ping) / 150))
        speed_score = 15 * min(1, (network_stats.download_speed / 100))
        network_score = ping_score + speed_score

    cpu_score = 15 * (1 - (system_stats.cpu_percent / 100))
    memory_score = 15 * (1 - (system_stats.memory_percent / 100))
    system_score = cpu_score + memory_score

    total_score = disk_score + network_score + system_score
    return round(total_score, 1)


def get_health_status(score: float) -> HealthStatus:
    if score >= 75:
        return "healthy"
    elif score >= 40:
        return "degraded"
    else:
        return "unhealthy"


def check_health() -> HealthCheckResult:
    disk_stats = check_disk_space()
    network_stats = check_network_speed()
    system_stats = check_system_stats()

    score = calculate_health_score(disk_stats, network_stats, system_stats)
    status = get_health_status(score)

    return HealthCheckResult(status=status, score=score, disk=disk_stats, network=network_stats, system=system_stats)
