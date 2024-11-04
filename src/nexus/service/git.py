import pathlib
import re
import shutil

from nexus.service.logger import logger

GIT_URL_PATTERN = re.compile(r"^(?:https?://|git@)(?:[\w.@:/\-~]+)(?:\.git)?/?$")


def validate_git_url(url: str) -> bool:
    """Validate git repository URL format"""
    return bool(GIT_URL_PATTERN.match(url))


def cleanup_repo(job_repo_dir: pathlib.Path) -> None:
    try:
        if job_repo_dir.exists():
            shutil.rmtree(job_repo_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error cleaning up repository directory {job_repo_dir}: {e}")
