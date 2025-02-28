import re
import subprocess

from nexus.server.core import exceptions as exc
from nexus.server.core import logger

__all__ = ["normalize_git_url", "async_cleanup_git_tag"]

GIT_URL_PATTERN = re.compile(r"^(?:https?://|git@)(?:[\w.@:/\-~]+)(?:\.git)?/?$")
SSH_PATTERN = re.compile(r"^git@(?P<host>[\w\.]+):(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
GIT_PROTOCOL_PATTERN = re.compile(r"^git://(?P<host>[\w\.]+)/(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
HTTPS_PATTERN = re.compile(r"^https://(?P<host>[\w\.]+)/(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
HOST_MAPPINGS = {
    "github.com": "github.com",
    "gitlab.com": "gitlab.com",
    "bitbucket.org": "bitbucket.org",
    "ssh.dev.azure.com": "dev.azure.com",
}


def _validate_git_url(url: str) -> None:
    if not url or not isinstance(url, str):
        valid = False
    valid = bool(GIT_URL_PATTERN.match(url.strip()))
    if not valid:
        raise exc.GitError(message="Invalid git repository URL format")


####################


@exc.handle_exception_async(
    subprocess.CalledProcessError, exc.GitError, message="Failed to clean up git tag", reraise=False
)
async def async_cleanup_git_tag(_logger: logger.NexusServerLogger, git_tag: str, git_repo_url: str) -> None:
    subprocess.run(["git", "push", git_repo_url, "--delete", git_tag], check=True, capture_output=True, text=True)
    _logger.info(f"Cleaned up git tag {git_tag} from {git_repo_url}")
    return None


def normalize_git_url(url: str) -> str:
    _validate_git_url(url)

    url = url.strip()

    # Already HTTPS format
    if HTTPS_PATTERN.match(url):
        return url.rstrip("/")

    # SSH format
    if match := SSH_PATTERN.match(url):
        host = match.group("host")
        path = match.group("path")
        if mapped_host := HOST_MAPPINGS.get(host):
            return f"https://{mapped_host}/{path}"
        raise exc.GitError(message=f"Unknown Git host: {host}")

    # Git protocol
    if match := GIT_PROTOCOL_PATTERN.match(url):
        host = match.group("host")
        path = match.group("path")
        if mapped_host := HOST_MAPPINGS.get(host):
            return f"https://{mapped_host}/{path}"
        raise exc.GitError(message=f"Unknown Git host: {host}")

    raise exc.GitError(message="Invalid Git URL format. Must be HTTPS, SSH, or Git protocol URL.")
