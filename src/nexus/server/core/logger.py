import logging
import logging.handlers
import pathlib as pl
import typing as tp

import colorlog as cl

__all__ = ["NexusServerLogger", "create_logger"]


class NexusServerLogger(logging.Logger):
    pass


logging.setLoggerClass(NexusServerLogger)


def create_logger(
    log_dir: pl.Path | None,
    name: str = "server",
    log_file: str = "server.log",
    log_level: str = "info",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
) -> NexusServerLogger:
    logging.setLoggerClass(NexusServerLogger)
    log_level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    numeric_log_level = log_level_map.get(log_level.lower(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(numeric_log_level)
    logger.handlers = []

    file_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_formatter = logging.Formatter(file_format, datefmt="%Y-%m-%d %H:%M:%S")

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / log_file
        log_file_path.touch()  # Ensures the file exists
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_file_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(numeric_log_level)
        logger.addHandler(file_handler)

    if console_output:
        console_format = "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s%(reset)s"
        console_formatter = cl.ColoredFormatter(
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
        console_handler.setLevel(numeric_log_level)
        logger.addHandler(console_handler)

    return tp.cast(NexusServerLogger, logger)
