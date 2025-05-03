import logging
import os
import sys

__all__ = [
    "debug",
    "info",
    "warning",
    "error",
    "critical",
    "exception",
]

_logger = logging.getLogger("nexus")

_env_log_level = os.environ.get("NS_LOG_LEVEL", "info").lower()
_level_map = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
_log_level = _level_map.get(_env_log_level, logging.INFO)
_logger.setLevel(_log_level)

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
_logger.addHandler(_handler)

debug = _logger.debug
info = _logger.info
warning = _logger.warning
error = _logger.error
critical = _logger.critical
exception = _logger.exception
