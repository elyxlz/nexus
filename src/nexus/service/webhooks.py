import asyncio
import os
import pathlib
from typing import Literal

import aiohttp
from pydantic import BaseModel

from nexus.service.job import get_job_logs
from nexus.service.logger import logger
from nexus.service.models import Job


class WebhookMessage(BaseModel):
    content: str
    username: str = "Nexus"


async def send_webhook(message: str) -> None:
    """Send a message to Discord webhook."""
    webhook_url = os.getenv("NEXUS_DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Discord webhook URL not configured")
        return

    try:
        webhook_data = WebhookMessage(content=message)
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_data.model_dump()) as response:
                if response.status != 204:
                    logger.error(f"Failed to send webhook: {response.status}")
    except Exception as e:
        logger.error(f"Error sending webhook: {e}")


def format_job_message(job: Job, event_type: Literal["started", "completed", "failed"]) -> str:
    """Format job information for webhook message."""
    user_mention = f"<@{job.user}>" if job.user else "No user assigned"

    base_info = [f"Job {job.id} {event_type}", f"Command: {job.command}", f"Git: {job.git_tag} ({job.git_repo_url})", f"User: {user_mention}"]

    if event_type == "started":
        base_info.append(f"GPU: {job.gpu_index}")
        if job.wandb_url:
            base_info.append(f"W&B: {job.wandb_url}")

    return "\n".join(base_info)


async def notify_job_started(job: Job) -> None:
    """Send webhook notification for job start after waiting for potential W&B URL."""
    # Wait 30 seconds to allow W&B URL to be populated
    await asyncio.sleep(30)
    message = format_job_message(job, "started")
    await send_webhook(message)


async def notify_job_completed(job: Job) -> None:
    """Send webhook notification for job completion."""
    message = format_job_message(job, "completed")
    await send_webhook(message)


async def notify_job_failed(job: Job, jobs_dir: pathlib.Path) -> None:
    """Send webhook notification for job failure with last few log lines."""
    base_message = format_job_message(job, "failed")

    # Add error message if available
    if job.error_message:
        base_message += f"\nError: {job.error_message}"

    # Add last few lines of logs
    logs = get_job_logs(job, jobs_dir)
    if logs:
        last_lines = "\n".join(logs.splitlines()[-5:])  # Get last 5 lines
        base_message += f"\n\nLast few log lines:\n```\n{last_lines}\n```"

    await send_webhook(base_message)
