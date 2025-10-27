import inspect
import typing as tp

from nexus.server.utils import logger

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
    "RETURN_FIRST_ARG",
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


class _ReturnFirstArg:
    pass


RETURN_FIRST_ARG = _ReturnFirstArg()


T = tp.TypeVar("T")
P = tp.ParamSpec("P")


def handle_exception(source_exception, target_exception=None, *, message="error", reraise=True, default_return=None):
    def deco(fn):
        if inspect.iscoroutinefunction(fn):

            async def wrapped_async(*a, **k):
                try:
                    return await fn(*a, **k)
                except source_exception as e:
                    full = f"{message}: {e}"
                    logger.exception(full)
                    if not reraise:
                        if default_return is RETURN_FIRST_ARG:
                            return a[0] if a else None
                        return default_return
                    raise (target_exception or type(e))(message=full) from e

            return wrapped_async
        else:

            def wrapped_sync(*a, **k):
                try:
                    return fn(*a, **k)
                except source_exception as e:
                    full = f"{message}: {e}"
                    logger.exception(full)
                    if not reraise:
                        if default_return is RETURN_FIRST_ARG:
                            return a[0] if a else None
                        return default_return
                    raise (target_exception or type(e))(message=full) from e

            return wrapped_sync

    return deco
