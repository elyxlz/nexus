import logging
import pathlib as pl
from logging.handlers import RotatingFileHandler

from colorlog import ColoredFormatter


def create_service_logger(
    log_dir: pl.Path = pl.Path.home() / ".nexus_service",
    name: str = "service",
    log_file: str = "service.log",
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
) -> logging.Logger:
    # Ensure the log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers = []

    file_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_formatter = logging.Formatter(file_format, datefmt="%Y-%m-%d %H:%M:%S")

    log_file_path = log_dir / log_file
    log_file_path.touch()  # Now this will succeed

    file_handler = RotatingFileHandler(
        filename=str(log_file_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_level)
    logger.addHandler(file_handler)

    if console_output:
        console_format = "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s%(reset)s"
        console_formatter = ColoredFormatter(
            console_format,
            datefmt="%Y-%m-%d %H:%M:%S",
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(log_level)
        logger.addHandler(console_handler)

    return logger


logger = create_service_logger()
