import pathlib as pl
import re
import shutil
import subprocess

from nexus.service import logger

# Patterns for different Git URL formats
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


def validate_git_url(url: str) -> bool:
    """Validate git repository URL format"""
    return bool(GIT_URL_PATTERN.match(url))


def cleanup_repo(logger: logger.NexusServiceLogger, jobs_dir: pl.Path, job_id: str) -> None:
    job_repo_dir = jobs_dir / job_id / "repo"
    if job_repo_dir.exists():
        shutil.rmtree(job_repo_dir, ignore_errors=True)
        logger.info(f"Successfully cleaned up {job_repo_dir}")


def cleanup_git_tag(logger: logger.NexusServiceLogger, git_tag: str, git_repo_url: str) -> None:
    subprocess.run(["git", "push", git_repo_url, "--delete", git_tag], check=True, capture_output=True, text=True)
    logger.info(f"Cleaned up git tag {git_tag} from {git_repo_url} for job {id}")


def normalize_git_url(url: str) -> str:
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
        raise ValueError(f"Unknown Git host: {host}")

    # Git protocol
    if match := GIT_PROTOCOL_PATTERN.match(url):
        host = match.group("host")
        path = match.group("path")
        if mapped_host := HOST_MAPPINGS.get(host):
            return f"https://{mapped_host}/{path}"
        raise ValueError(f"Unknown Git host: {host}")

    raise ValueError("Invalid Git URL format. Must be HTTPS, SSH, or Git protocol URL.")
