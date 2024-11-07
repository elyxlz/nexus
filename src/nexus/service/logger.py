import logging
import os
import pathlib
from logging.handlers import RotatingFileHandler

from colorlog import ColoredFormatter

from nexus.service.config import load_config


def create_service_logger(
    log_dir: str,
    name: str = "service",
    log_file: str = "service.log",
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers = []

    # File handler with standard formatting
    file_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_formatter = logging.Formatter(file_format, datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_level)
    logger.addHandler(file_handler)

    if console_output:
        # Colored console handler
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


config = load_config()
nexus_dir = pathlib.Path.home() / ".nexus_service"
log_level = getattr(logging, config.log_level.upper())
logger = create_service_logger(str(nexus_dir), log_level=log_level)
