import shutil
import re
import subprocess
from pathlib import Path

from nexus.service.logger import logger


GIT_URL_PATTERN = re.compile(r"^(?:https?://|git@)(?:[\w.@:/\-~]+)(?:\.git)?/?$")


def validate_git_url(url: str) -> bool:
    """Validate git repository URL format"""
    return bool(GIT_URL_PATTERN.match(url))


def clone_repository(repo_url: str, tag: str, target_dir: Path) -> None:
    try:
        logger.info(f"Cloning {repo_url} (tag: {tag}) into {target_dir}")
        # Single shallow clone command with the exact tag we want
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",  # Shallow clone
                "--single-branch",  # Only clone the one branch/tag
                "--no-tags",  # Don't fetch any other tags
                "--branch",
                tag,  # Specify the tag we want
                "--quiet",
                repo_url,
                str(target_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    except subprocess.CalledProcessError as e:
        error_msg = f"Git operation failed: {e.stderr if e.stderr else str(e)}"
        logger.error(error_msg)
        cleanup_repo(target_dir)
        raise Exception(error_msg)


def cleanup_repo(job_repo_dir: Path) -> None:
    try:
        if job_repo_dir.exists():
            shutil.rmtree(job_repo_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error cleaning up repository directory {job_repo_dir}: {e}")
