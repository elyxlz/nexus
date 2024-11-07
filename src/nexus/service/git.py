import pathlib
import re
import shutil
import subprocess

from nexus.service import models
from nexus.service.logger import logger

GIT_URL_PATTERN = re.compile(r"^(?:https?://|git@)(?:[\w.@:/\-~]+)(?:\.git)?/?$")

# Patterns for different Git URL formats
SSH_PATTERN = re.compile(r"^git@(?P<host>[\w\.]+):(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
GIT_PROTOCOL_PATTERN = re.compile(r"^git://(?P<host>[\w\.]+)/(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
HTTPS_PATTERN = re.compile(r"^https://(?P<host>[\w\.]+)/(?P<path>[\w\-\.~]+/[\w\-\.~]+?)(?:\.git)?/?$")
HOST_MAPPINGS = {"github.com": "github.com", "gitlab.com": "gitlab.com", "bitbucket.org": "bitbucket.org", "ssh.dev.azure.com": "dev.azure.com"}


def validate_git_url(url: str) -> bool:
    """Validate git repository URL format"""
    return bool(GIT_URL_PATTERN.match(url))


def cleanup_repo(jobs_dir: pathlib.Path, job_id: str) -> None:
    job_repo_dir = jobs_dir / job_id / "repo"
    try:
        if job_repo_dir.exists():
            shutil.rmtree(job_repo_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error cleaning up repository directory {job_repo_dir}: {e}")


def cleanup_git_tag(completed_job: models.Job, running_jobs: list[models.Job]) -> None:
    if not (completed_job.git_tag and completed_job.git_repo_url):
        return

    # Check if any other running jobs use this tag
    if any(job.git_tag == completed_job.git_tag for job in running_jobs):
        return

    try:
        # Delete tag from remote, using the specific repository
        subprocess.run(
            ["git", "push", completed_job.git_repo_url, "--delete", completed_job.git_tag], check=True, capture_output=True, text=True
        )
        logger.info(f"Cleaned up git tag {completed_job.git_tag} from {completed_job.git_repo_url} for job {completed_job.id}")
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to cleanup git tag {completed_job.git_tag} from {completed_job.git_repo_url} for job {completed_job.id}: {e.stderr}"
        )
    except Exception as e:
        logger.error(f"Unexpected error cleaning up git tag {completed_job.git_tag} from {completed_job.git_repo_url}: {e}")


def normalize_git_url(url: str) -> str:
    """
    Normalize a Git URL to HTTPS format.

    Args:
        url: Git URL in any supported format (HTTPS, SSH, Git protocol)

    Returns:
        Normalized HTTPS URL

    Raises:
        ValueError: If URL format is invalid or host is unknown
    """
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
