import shutil
import subprocess
from pathlib import Path

from nexus.service.logger import logger


class GitError(Exception):
    """Custom exception for git operations"""

    pass


def clone_repository(repo_url: str, tag: str, target_dir: Path) -> None:
    try:
        # Clone the repository
        logger.info(f"Cloning {repo_url} into {target_dir}")
        subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(target_dir)],
            check=True,
            capture_output=True,
            text=True,
        )

        # Checkout the specific tag
        logger.info(f"Checking out tag {tag}")
        subprocess.run(
            ["git", "checkout", "--quiet", tag],
            check=True,
            capture_output=True,
            text=True,
            cwd=target_dir,
        )

    except subprocess.CalledProcessError as e:
        error_msg = f"Git operation failed: {e.stderr if e.stderr else str(e)}"
        logger.error(error_msg)
        cleanup_repo(target_dir)
        raise GitError(error_msg)


def cleanup_repo(repo_dir: Path) -> None:
    try:
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error cleaning up repository directory {repo_dir}: {e}")
