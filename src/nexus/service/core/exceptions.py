import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = [
    "NexusServiceError",
    "ConfigurationError",
    "ServiceError",
    "GPUError",
    "GitError",
    "DatabaseError",
    "JobError",
    "handle_exception",
]


class NexusServiceError(Exception):
    """Base exception for all Nexus errors."""

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


T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def handle_exception(
    source_exception: type[Exception],
    target_exception: type[NexusServiceError] | None = None,
    message: str = "An error occurred",
    reraise: bool = True,
    default_return: Any = None,
) -> Callable[[Callable[..., T]], Callable[..., T | Any]]:
    """
    Decorator for handling exceptions on functions that accept a _logger keyword parameter.

    This decorator requires that the wrapped function accepts a '_logger' parameter.

    Args:
        source_exception: The exception type to catch
        target_exception: Optional NexusServiceError subclass to convert to
        message: Error message to log
        reraise: Whether to reraise the exception (always True if target_exception is provided)
        default_return: Value to return if not reraising

    Returns:
        Decorated function

    Raises:
        ValueError: If the function being decorated doesn't accept a '_logger' parameter
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T | Any]:
        # Validate that the function accepts a _logger parameter
        sig = inspect.signature(func)
        if "_logger" not in sig.parameters:
            raise ValueError(f"Function '{func.__name__}' must accept a '_logger' parameter to use @handle_exception")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T | Any:
            # Check if _logger was passed as a keyword argument
            if "_logger" not in kwargs:
                raise ValueError(f"Missing required '_logger' keyword argument when calling {func.__name__}")

            logger = kwargs["_logger"]

            try:
                return func(*args, **kwargs)
            except Exception as e:
                if isinstance(e, source_exception):
                    error_msg = f"{message}: {str(e)}"
                    logger.error(error_msg)

                    # Convert exception if target_exception is provided
                    if target_exception is not None:
                        new_err_msg = f"{error_msg} (converted from {type(e).__name__})"
                        # All target exceptions are NexusServiceError subclasses that take a message parameter
                        raise target_exception(message=new_err_msg) from e

                    # Handle reraise option if no target_exception
                    if not reraise:
                        return default_return

                    # Reraise the original exception
                    raise

                # If not the specified exception type, just let it propagate
                raise

        return wrapper

    return decorator
