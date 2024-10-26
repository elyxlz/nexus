import logging
import logging.handlers
import os
import glob
import typing


def create_service_logger(
    log_dir: str,
    name: str = "service",
    log_file: str = "service.log",
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
    log_format: str | None = None,
) -> logging.Logger:
    service_log_dir = os.path.join(log_dir, "service")
    os.makedirs(service_log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers = []
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(service_log_dir, log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    logger.addHandler(file_handler)
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)
        logger.addHandler(console_handler)
    return logger


def read_latest_service_logs(
    log_dir: str,
    num_lines: int = 50,
    log_file: str = "service.log",
    sort_order: typing.Literal["newest", "oldest"] = "newest",
) -> list[str]:
    service_log_dir = os.path.join(log_dir, "service")
    log_path = os.path.join(service_log_dir, log_file)
    if not os.path.exists(log_path):
        return [f"No log file found at {log_path}"]
    log_files = [log_path] + glob.glob(f"{log_path}.*")
    log_files.sort(key=os.path.getmtime, reverse=True)
    all_lines = []
    remaining_lines = num_lines
    for log_file in log_files:
        if remaining_lines <= 0:
            break
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if sort_order == "newest":
                    lines = list(reversed(lines))
                all_lines.extend(lines[:remaining_lines])
                remaining_lines -= len(lines)
        except Exception as e:
            return [f"Error reading log file {log_file}: {str(e)}"]
    all_lines = all_lines[:num_lines]
    if sort_order == "newest":
        all_lines.reverse()
    return [line.strip() for line in all_lines]
