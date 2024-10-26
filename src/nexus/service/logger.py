import logging
from logging.handlers import RotatingFileHandler
import os

from nexus.service.config import load_config


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
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers = []
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, log_file),
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


config = load_config()
logger = create_service_logger(str(config.log_dir))