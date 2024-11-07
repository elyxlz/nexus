import datetime
import json
import os
import pathlib
import typing

import aiohttp
import pydantic as pyd

from nexus.service.job import get_job_logs
from nexus.service.logger import logger
from nexus.service.models import Job

EMOJI_MAPPING = {
    "started": ":rocket:",
    "completed": ":checkered_flag:",
    "failed": ":interrobang:",
}


class WebhookMessage(pyd.BaseModel):
    content: str
    embeds: list[dict] | None = None
    username: str = "Nexus"


# Simple data structure for webhook state
class WebhookState(pyd.BaseModel):
    message_ids: dict[str, str] = {}  # job_id -> message_id


def load_webhook_state(state_path: pathlib.Path) -> WebhookState:
    """Load webhook state from disk."""
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            return WebhookState(message_ids=data.get("message_ids", {}))
        except Exception as e:
            logger.error(f"Error loading webhook state: {e}")
    return WebhookState()


def save_webhook_state(state: WebhookState, state_path: pathlib.Path) -> None:
    """Save webhook state to disk."""
    try:
        state_path.write_text(json.dumps({"message_ids": state.message_ids}))
    except Exception as e:
        logger.error(f"Error saving webhook state: {e}")


def format_job_message_for_webhook(job: Job, event_type: typing.Literal["started", "completed", "failed"]) -> dict:
    """Format job information for webhook message with rich embeds."""

    if job.discord_id:
        user_mention = f"<@{job.discord_id}>"
    elif job.user:
        user_mention = job.user
    else:
        user_mention = "No user assigned"

    message_title = f"{EMOJI_MAPPING[event_type]} - **Job {job.id} {event_type} on GPU {job.gpu_index}** - {user_mention}"

    # Prepare field values
    command = job.command or "N/A"
    git_info = f"{job.git_tag or ''} ({job.git_repo_url or 'N/A'})"
    gpu_index = str(job.gpu_index) if job.gpu_index is not None else "N/A"
    wandb_url = "Pending ..." if event_type == "started" and not job.wandb_url else (job.wandb_url or "Not Found")

    fields = [
        {"name": "Command", "value": command},
        {"name": "W&B", "value": wandb_url},
        {"name": "Git", "value": git_info},
        {"name": "User", "value": job.user, "inline": True},
        {"name": "GPU", "value": gpu_index, "inline": True},
    ]

    if job.error_message and event_type in ["completed", "failed"]:
        fields.insert(1, {"name": "Error Message", "value": job.error_message})

    return {
        "content": message_title,
        "embeds": [
            {
                "fields": fields,
                "color": 4915310,
                "footer": {"text": f"Job Status Update • {job.id}"},
                "timestamp": datetime.datetime.now().isoformat(),
            }
        ],
    }


async def send_webhook(message_data: dict, wait: bool = False) -> str | None:
    """Send a message to Discord webhook. Returns message ID if wait=True."""
    webhook_url = os.getenv("NEXUS_DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Discord webhook URL not configured")
        return None

    try:
        webhook_data = WebhookMessage(**message_data)
        params = {"wait": "true"} if wait else {}

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_data.model_dump(), params=params) as response:
                if response.status == 204 or response.status == 200:
                    if wait:
                        data = await response.json()
                        return data.get("id")
                    return None
                else:
                    logger.error(f"Failed to send webhook: Status {response.status}, Message: {await response.text()}")
                    return None
    except Exception as e:
        logger.error(f"Error sending webhook: {e}")
        return None


async def edit_webhook_message(message_id: str, message_data: dict) -> bool:
    """Edit an existing webhook message."""
    webhook_url = os.getenv("NEXUS_DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return False

    edit_url = f"{webhook_url}/messages/{message_id}"

    try:
        webhook_data = WebhookMessage(**message_data)
        async with aiohttp.ClientSession() as session:
            async with session.patch(edit_url, json=webhook_data.model_dump()) as response:
                return response.status == 200
    except Exception as e:
        logger.error(f"Error editing webhook message: {e}")
        return False


async def notify_job_started(job: Job) -> None:
    """Send webhook notification for job start and store message ID."""
    message_data = format_job_message_for_webhook(job, "started")

    # Send with wait=True to get message ID
    message_id = await send_webhook(message_data, wait=True)

    if message_id:
        # Update webhook state
        state_path = pathlib.Path.home() / ".nexus_service" / "webhook_state.json"
        webhook_state = load_webhook_state(state_path)
        webhook_state.message_ids[job.id] = message_id
        save_webhook_state(webhook_state, state_path)


async def update_job_wandb(job: Job) -> None:
    """Update job webhook message with W&B URL if found."""
    if not job.wandb_url:
        logger.debug(f"No W&B URL found for job {job.id}. Skipping update.")
        return

    state_path = pathlib.Path.home() / ".nexus_service" / "webhook_state.json"
    webhook_state = load_webhook_state(state_path)
    message_id = webhook_state.message_ids.get(job.id)

    if message_id:
        message_data = format_job_message_for_webhook(job, "started")
        success = await edit_webhook_message(message_id, message_data)
        if success:
            logger.info(f"Updated webhook message for job {job.id} with W&B URL")


async def notify_job_completed(job: Job, jobs_dir: pathlib.Path | None = None) -> None:
    """Send webhook notification for job completion."""
    message_data = format_job_message_for_webhook(job, "completed")
    await send_webhook(message_data)


async def notify_job_failed(job: Job, jobs_dir: pathlib.Path) -> None:
    """Send webhook notification for job failure with last few log lines."""
    message_data = format_job_message_for_webhook(job, "failed")

    # Add last few lines of logs
    last_lines = get_job_logs(job.id, jobs_dir, last_n_lines=10)
    if last_lines:
        message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": f"```\n{last_lines}\n```"})

    await send_webhook(message_data)
