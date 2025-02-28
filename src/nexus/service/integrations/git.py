import pathlib as pl
import re
import shutil
import subprocess

from nexus.service.core import exceptions as exc
from nexus.service.core import logger

__all__ = ["validate_git_url", "normalize_git_url", "async_cleanup_repo", "async_cleanup_git_tag"]

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


async def async_cleanup_repo(_logger: logger.NexusServiceLogger, job_dir: pl.Path | None) -> None:
    if job_dir is None:
        return None

    job_repo_dir = job_dir / "repo"
    if job_repo_dir.exists():
        shutil.rmtree(job_repo_dir, ignore_errors=True)
        _logger.info(f"Successfully cleaned up {job_repo_dir}")


@exc.handle_exception(subprocess.CalledProcessError, exc.GitError, message="Failed to clean up git tag", reraise=False)
async def async_cleanup_git_tag(_logger: logger.NexusServiceLogger, git_tag: str, git_repo_url: str) -> None:
    subprocess.run(["git", "push", git_repo_url, "--delete", git_tag], check=True, capture_output=True, text=True)
    _logger.info(f"Cleaned up git tag {git_tag} from {git_repo_url}")
    return None


def validate_git_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return bool(GIT_URL_PATTERN.match(url.strip()))


def normalize_git_url(url: str) -> str:
    if not url:
        raise exc.GitError(message="Git URL cannot be empty")

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
