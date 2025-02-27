import functools
import typing as tp
from collections import abc

from nexus.service.core import logger

__all__ = [
    "NexusServiceError",
    "ConfigurationError",
    "ServiceError",
    "GPUError",
    "GitError",
    "DatabaseError",
    "JobError",
    "WandBError",
    "NotificationError",
    "handle_exception",
]


class NexusServiceError(Exception):
    ERROR_CODE = "NEXUS_ERROR"

    def __init__(self, message: str | None = None):
        self.code = self.__class__.ERROR_CODE
        self.message = message or f"{self.code} error occurred"
        super().__init__(self.message)


class ConfigurationError(NexusServiceError):
    ERROR_CODE = "CONFIG_ERROR"


class ServiceError(NexusServiceError):
    ERROR_CODE = "SERVICE_ERROR"


class GPUError(NexusServiceError):
    ERROR_CODE = "GPU_ERROR"


class GitError(NexusServiceError):
    ERROR_CODE = "GIT_ERROR"


class DatabaseError(NexusServiceError):
    ERROR_CODE = "DB_ERROR"


class JobError(NexusServiceError):
    ERROR_CODE = "JOB_ERROR"


class WandBError(NexusServiceError):
    ERROR_CODE = "WANDB_ERROR"


class NotificationError(NexusServiceError):
    ERROR_CODE = "WEBHOOK_ERROR"


T = tp.TypeVar("T")
P = tp.ParamSpec("P")  # This captures the parameter specification of the wrapped function


def handle_exception(
    source_exception: type[Exception],
    target_exception: type[NexusServiceError] | None = None,
    message: str = "An error occurred",
    reraise: bool = True,
    default_return: tp.Any = None,
) -> abc.Callable[[abc.Callable[P, T]], abc.Callable[P, T]]:  # Note the P here
    def decorator(func: abc.Callable[P, T]) -> abc.Callable[P, T]:  # And here
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:  # And here
            _logger = None

            for arg in args:
                if isinstance(arg, logger.NexusServiceLogger):
                    _logger = arg
                    break

            if _logger is None:
                for arg_name, arg_value in kwargs.items():
                    if isinstance(arg_value, logger.NexusServiceLogger):
                        _logger = arg_value
                        break

            if _logger is None:
                raise ValueError(f"Function '{func.__name__}' requires a NexusServiceLogger parameter")

            try:
                return func(*args, **kwargs)
            except Exception as e:
                if isinstance(e, source_exception):
                    error_msg = f"{message}: {str(e)}"
                    _logger.error(error_msg)

                    if target_exception is not None:
                        new_err_msg = f"{error_msg} (converted from {type(e).__name__})"
                        raise target_exception(message=new_err_msg) from e

                    if not reraise:
                        return tp.cast(T, default_return)

                    raise

                raise

        return wrapper

    return decorator
