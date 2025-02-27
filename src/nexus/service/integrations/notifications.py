import dataclasses as dc
import datetime
import json
import typing as tp

import aiohttp
import pydantic as pyd

from nexus.service.core import exceptions as exc
from nexus.service.core import logger, schemas

__all__ = ["notify_job_started", "update_job_wandb", "notify_job_completed", "notify_job_failed"]

EMOJI_MAPPING = {"started": ":rocket:", "completed": ":checkered_flag:", "failed": ":interrobang:"}


class NotificationMessage(pyd.BaseModel):
    content: str
    embeds: list[dict] | None = None
    username: str = "Nexus"


def format_job_message_for_notification(
    job: schemas.Job, event_type: tp.Literal["started", "completed", "failed"]
) -> dict:
    discord_id = job.env.get("DISCORD_ID")
    if discord_id:
        user_mention = f"<@{discord_id}>"
    elif job.user:
        user_mention = job.user
    else:
        user_mention = "No user assigned"

    message_title = (
        f"{EMOJI_MAPPING[event_type]} - **Job {job.id} {event_type} on GPU {job.gpu_index}** - {user_mention}"
    )

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


@exc.handle_exception(pyd.ValidationError, exc.NotificationError, message="Invalid notification message format")
@exc.handle_exception(aiohttp.ClientError, exc.NotificationError, message="Discord notification request failed")
@exc.handle_exception(
    json.JSONDecodeError,
    exc.NotificationError,
    message="Invalid JSON response from Discord notification",
)
async def send_notification(
    _logger: logger.NexusServiceLogger, notification_url: str, message_data: dict, wait: bool = False
) -> str | None:
    if not notification_url:
        _logger.warning("Discord notification URL not provided")
        raise exc.NotificationError(message="Discord notification URL not provided")

    notification_data = NotificationMessage(**message_data)
    params = {"wait": "true"} if wait else {}

    async with aiohttp.ClientSession() as session:
        async with session.post(notification_url, json=notification_data.model_dump(), params=params) as response:
            if response.status == 204 or response.status == 200:
                if wait:
                    data = await response.json()
                    return data.get("id")
                return None
            else:
                error_msg = f"Failed to send notification: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)


@exc.handle_exception(pyd.ValidationError, exc.NotificationError, message="Invalid notification message format")
@exc.handle_exception(aiohttp.ClientError, exc.NotificationError, message="Discord notification edit request failed")
async def edit_notification_message(
    _logger: logger.NexusServiceLogger, notification_url: str, message_id: str, message_data: dict
) -> bool:
    if not notification_url:
        _logger.warning("Discord notification URL not provided")
        raise exc.NotificationError(message="Discord notification URL not provided")

    edit_url = f"{notification_url}/messages/{message_id}"
    notification_data = NotificationMessage(**message_data)

    async with aiohttp.ClientSession() as session:
        async with session.patch(edit_url, json=notification_data.model_dump()) as response:
            if response.status != 200:
                error_msg = f"Failed to edit notification: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)
            return True


@exc.handle_exception(exc.NotificationError, message="Error notifying job start")
async def notify_job_started(_logger: logger.NexusServiceLogger, job: schemas.Job) -> schemas.Job:
    message_data = format_job_message_for_notification(job, "started")

    # Send with wait=True to get message ID
    message_id = await send_notification(_logger, notification_url, message_data, wait=True)

    if message_id:
        # Return updated job with notification message ID
        return dc.replace(job, notification_message_id=message_id)
    return job


@exc.handle_exception(exc.NotificationError, message="Error updating job W&B info")
async def update_job_wandb(_logger: logger.NexusServiceLogger, job: schemas.Job) -> None:
    if not job.wandb_url or not job.notification_message_id:
        _logger.debug(f"No W&B URL or notification message ID found for job {job.id}. Skipping update.")
        return

    message_data = format_job_message_for_notification(job, "started")
    await edit_notification_message(_logger, notification_url, job.discord_start_notification_message_id, message_data)
    _logger.info(f"Updated notification message for job {job.id} with W&B URL")


@exc.handle_exception(exc.NotificationError, message="Error notifying job completion")
async def notify_job_completed(_logger: logger.NexusServiceLogger, job: schemas.Job) -> None:
    message_data = format_job_message_for_notification(job, "completed")
    await send_notification(_logger, notification_url, message_data)


@exc.handle_exception(exc.NotificationError, message="Error notifying job failure")
async def notify_job_failed(_logger: logger.NexusServiceLogger, job: schemas.Job, job_logs: str | None) -> None:
    message_data = format_job_message_for_notification(job, "failed")

    # Add last few lines of logs
    if job_logs:
        message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": f"```\n{job_logs}\n```"})

    await send_notification(_logger, message_data=message_data)
