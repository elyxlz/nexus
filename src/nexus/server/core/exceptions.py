import functools
import typing as tp
from collections import abc

from nexus.server.core import logger

__all__ = [
    "NexusServerError",
    "ConfigurationError",
    "ServerError",
    "GPUError",
    "GitError",
    "DatabaseError",
    "JobError",
    "WandBError",
    "NotificationError",
    "NotFoundError",
    "JobNotFoundError",
    "GPUNotFoundError",
    "InvalidRequestError",
    "InvalidJobStateError",
    "handle_exception",
    "handle_exception_async",
]


class NexusServerError(Exception):
    ERROR_CODE = "NEXUS_ERROR"
    STATUS_CODE = 500

    def __init__(self, message: str | None = None):
        self.code = self.__class__.ERROR_CODE
        self.message = message or f"{self.code} error occurred"
        super().__init__(self.message)


class ConfigurationError(NexusServerError):
    ERROR_CODE = "CONFIG_ERROR"


class ServerError(NexusServerError):
    ERROR_CODE = "SERVER_ERROR"


class GPUError(NexusServerError):
    ERROR_CODE = "GPU_ERROR"


class GitError(NexusServerError):
    ERROR_CODE = "GIT_ERROR"


class DatabaseError(NexusServerError):
    ERROR_CODE = "DB_ERROR"


# Not Found errors (404)
class NotFoundError(NexusServerError):
    ERROR_CODE = "NOT_FOUND"
    STATUS_CODE = 404


class JobNotFoundError(NotFoundError):
    ERROR_CODE = "JOB_NOT_FOUND"


class GPUNotFoundError(NotFoundError):
    ERROR_CODE = "GPU_NOT_FOUND"


# Invalid request errors (400)
class InvalidRequestError(NexusServerError):
    ERROR_CODE = "INVALID_REQUEST"
    STATUS_CODE = 400


class JobError(NexusServerError):
    ERROR_CODE = "JOB_ERROR"


class InvalidJobStateError(InvalidRequestError):
    ERROR_CODE = "INVALID_JOB_STATE"


class WandBError(NexusServerError):
    ERROR_CODE = "WANDB_ERROR"


class NotificationError(NexusServerError):
    ERROR_CODE = "WEBHOOK_ERROR"


T = tp.TypeVar("T")
P = tp.ParamSpec("P")  # This captures the parameter specification of the wrapped function


def handle_exception(
    source_exception: type[Exception],
    target_exception: type[NexusServerError] | None = None,
    message: str = "An error occurred",
    reraise: bool = True,
    default_return: tp.Any = None,
) -> abc.Callable[[abc.Callable[P, T]], abc.Callable[P, T]]:  # Note the P here
    def decorator(func: abc.Callable[P, T]) -> abc.Callable[P, T]:  # And here
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:  # And here
            _logger = None

            for arg in args:
                if isinstance(arg, logger.NexusServerLogger):
                    _logger = arg
                    break

            if _logger is None:
                for arg_name, arg_value in kwargs.items():
                    if isinstance(arg_value, logger.NexusServerLogger):
                        _logger = arg_value
                        break

            if _logger is None:
                raise ValueError(f"Function '{func.__name__}' requires a NexusServerLogger parameter")

            try:
                return func(*args, **kwargs)
            except Exception as e:
                if isinstance(e, source_exception):
                    error_msg = f"{message}: {str(e)}"
                    _logger.exception(error_msg)

                    if not reraise:
                        return tp.cast(T, default_return)

                    if target_exception is not None:
                        new_err_msg = f"{error_msg} (converted from {type(e).__name__})"
                        raise target_exception(message=new_err_msg) from e

                    raise

                raise

        return wrapper

    return decorator


def handle_exception_async(
    source_exception: type[Exception],
    target_exception: type[NexusServerError] | None = None,
    message: str = "An error occurred",
    reraise: bool = True,
    default_return: tp.Any = None,
) -> abc.Callable[[abc.Callable[P, tp.Awaitable[T]]], abc.Callable[P, tp.Awaitable[T]]]:
    def decorator(func: abc.Callable[P, tp.Awaitable[T]]) -> abc.Callable[P, tp.Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            _logger = None

            for arg in args:
                if isinstance(arg, logger.NexusServerLogger):
                    _logger = arg
                    break

            if _logger is None:
                for arg_name, arg_value in kwargs.items():
                    if isinstance(arg_value, logger.NexusServerLogger):
                        _logger = arg_value
                        break

            if _logger is None:
                raise ValueError(f"Function '{func.__name__}' requires a NexusServerLogger parameter")

            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if isinstance(e, source_exception):
                    error_msg = f"{message}: {str(e)}"
                    _logger.exception(error_msg)

                    if not reraise:
                        return tp.cast(T, default_return)

                    if target_exception is not None:
                        new_err_msg = f"{error_msg} (converted from {type(e).__name__})"
                        raise target_exception(message=new_err_msg) from e

                    raise

                raise

        return wrapper

    return decorator
