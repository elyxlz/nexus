import pathlib
import re
import shutil
import subprocess

from nexus.service import models
from nexus.service.logger import logger

GIT_URL_PATTERN = re.compile(r"^(?:https?://|git@)(?:[\w.@:/\-~]+)(?:\.git)?/?$")


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
