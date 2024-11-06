import os
import pathlib
import typing
from datetime import datetime

import aiohttp
import pydantic as pyd

from nexus.service.job import get_job_logs
from nexus.service.logger import logger
from nexus.service.models import Job

# Discord user mapping for mentions
DISCORD_USER_MAPPING = {
    "elyxlz": "223864514326560768",  # TODO: figure out what to do
}

# Emoji mapping for different job states
EMOJI_MAPPING = {
    "started": ":rocket:",
    "completed": ":checkered_flag:",
    "failed": ":interrobang:",
}


class WebhookMessage(pyd.BaseModel):
    content: str
    embeds: list[dict] | None = None
    username: str = "Nexus"


def format_job_message_for_webhook(job: Job, event_type: typing.Literal["started", "completed", "failed"]) -> dict:
    """Format job information for webhook message with rich embeds."""
    user_mention = f"@{DISCORD_USER_MAPPING[job.user]}" if (job.user and job.user in DISCORD_USER_MAPPING) else "No user assigned"

    message_title = f"{EMOJI_MAPPING[event_type]} - **Job {job.id} {event_type} on GPU {job.gpu_index}** - {user_mention}"

    # Prepare field values, using 'N/A' as fallback
    command = job.command or "N/A"
    git_info = f"{job.git_tag or ''} ({job.git_repo_url or 'N/A'})"
    gpu_index = str(job.gpu_index or "N/A")
    wandb_url = job.wandb_url or "N/A"

    # Build the embed fields list
    fields = [
        {
            "name": "Command",
            "value": command,
        },
        {
            "name": "Git",
            "value": git_info,
        },
        {
            "name": "W&B",
            "value": wandb_url,
        },
        {"name": "User", "value": user_mention, "inline": True},
        {"name": "GPU", "value": gpu_index, "inline": True},
    ]

    # Add error message if available for completed or failed jobs
    if job.error_message and event_type in ["completed", "failed"]:
        fields.insert(0, {"name": "Error Message", "value": job.error_message})

    return {
        "content": message_title,
        "embeds": [
            {"fields": fields, "color": 4915310, "footer": {"text": f"Job Status Update • {job.id}"}, "timestamp": datetime.now().isoformat()}
        ],
    }


async def send_webhook(message_data: dict) -> None:
    """Send a message to Discord webhook."""
    webhook_url = os.getenv("NEXUS_DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Discord webhook URL not configured")
        return

    try:
        webhook_data = WebhookMessage(**message_data)
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_data.model_dump()) as response:
                if response.status != 204:
                    logger.error(f"Failed to send webhook: {response.status}")
    except Exception as e:
        logger.error(f"Error sending webhook: {e}")


async def notify_job_started(job: Job) -> None:
    """Send webhook notification for job start after waiting for potential W&B URL."""
    # Wait 30 seconds to allow W&B URL to be populated
    message_data = format_job_message_for_webhook(job, "started")
    await send_webhook(message_data)


async def notify_job_completed(job: Job, jobs_dir: pathlib.Path | None = None) -> None:
    """Send webhook notification for job completion."""
    message_data = format_job_message_for_webhook(job, "completed")
    await send_webhook(message_data)


async def notify_job_failed(job: Job, jobs_dir: pathlib.Path) -> None:
    """Send webhook notification for job failure with last few log lines."""
    message_data = format_job_message_for_webhook(job, "failed")

    # Add last few lines of logs
    last_lines = get_job_logs(job.id, jobs_dir, last_n_lines=5)
    if last_lines:
        message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": f"```\n{last_lines}\n```"})

    await send_webhook(message_data)
