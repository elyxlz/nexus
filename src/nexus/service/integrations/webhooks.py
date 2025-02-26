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


class WebhookMessage(pyd.BaseModel):
    content: str
    embeds: list[dict] | None = None
    username: str = "Nexus"


def format_job_message_for_webhook(job: schemas.Job, event_type: tp.Literal["started", "completed", "failed"]) -> dict:
    if job.discord_id:
        user_mention = f"<@{job.discord_id}>"
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


@exc.handle_exception(pyd.ValidationError, exc.WebhookError, message="Invalid webhook message format")
@exc.handle_exception(aiohttp.ClientError, exc.WebhookError, message="Discord webhook request failed")
@exc.handle_exception(json.JSONDecodeError, exc.WebhookError, message="Invalid JSON response from Discord webhook")
async def send_webhook(
    _logger: logger.NexusServiceLogger, webhook_url: str, message_data: dict, wait: bool = False
) -> str | None:
    if not webhook_url:
        _logger.warning("Discord webhook URL not provided")
        raise exc.WebhookError(message="Discord webhook URL not provided")

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
                error_msg = f"Failed to send webhook: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.WebhookError(message=error_msg)


@exc.handle_exception(pyd.ValidationError, exc.WebhookError, message="Invalid webhook message format")
@exc.handle_exception(aiohttp.ClientError, exc.WebhookError, message="Discord webhook edit request failed")
async def edit_webhook_message(
    _logger: logger.NexusServiceLogger, webhook_url: str, message_id: str, message_data: dict
) -> bool:
    if not webhook_url:
        _logger.warning("Discord webhook URL not provided")
        raise exc.WebhookError(message="Discord webhook URL not provided")

    edit_url = f"{webhook_url}/messages/{message_id}"
    webhook_data = WebhookMessage(**message_data)

    async with aiohttp.ClientSession() as session:
        async with session.patch(edit_url, json=webhook_data.model_dump()) as response:
            if response.status != 200:
                error_msg = f"Failed to edit webhook: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.WebhookError(message=error_msg)
            return True


@exc.handle_exception(exc.WebhookError, message="Error notifying job start")
async def notify_job_started(_logger: logger.NexusServiceLogger, webhook_url: str, job: schemas.Job) -> schemas.Job:
    message_data = format_job_message_for_webhook(job, "started")

    # Send with wait=True to get message ID
    message_id = await send_webhook(_logger, webhook_url, message_data, wait=True)

    if message_id:
        # Return updated job with webhook message ID
        return dc.replace(job, webhook_message_id=message_id)
    return job


@exc.handle_exception(exc.WebhookError, message="Error updating job W&B info")
async def update_job_wandb(_logger: logger.NexusServiceLogger, webhook_url: str, job: schemas.Job) -> None:
    if not job.wandb_url or not job.webhook_message_id:
        _logger.debug(f"No W&B URL or webhook message ID found for job {job.id}. Skipping update.")
        return

    message_data = format_job_message_for_webhook(job, "started")
    await edit_webhook_message(_logger, webhook_url, job.webhook_message_id, message_data)
    _logger.info(f"Updated webhook message for job {job.id} with W&B URL")


@exc.handle_exception(exc.WebhookError, message="Error notifying job completion")
async def notify_job_completed(_logger: logger.NexusServiceLogger, webhook_url: str, job: schemas.Job) -> None:
    message_data = format_job_message_for_webhook(job, "completed")
    await send_webhook(_logger, webhook_url, message_data)


@exc.handle_exception(exc.WebhookError, message="Error notifying job failure")
async def notify_job_failed(
    _logger: logger.NexusServiceLogger, webhook_url: str, job: schemas.Job, job_logs: str | None
) -> None:
    message_data = format_job_message_for_webhook(job, "failed")

    # Add last few lines of logs
    if job_logs:
        message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": f"```\n{job_logs}\n```"})

    await send_webhook(_logger, webhook_url, message_data)
