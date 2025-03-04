import dataclasses as dc
import datetime
import json
import typing as tp

import aiohttp
import pydantic as pyd

from nexus.server.core import exceptions as exc
from nexus.server.core import logger, schemas
from nexus.server.core.job import async_get_job_logs

__all__ = ["notify_job_action", "update_notification_with_wandb"]

JobAction = tp.Literal["started", "completed", "failed", "killed"]

EMOJI_MAPPING = {
    "started": ":rocket:",
    "completed": ":checkered_flag:",
    "failed": ":interrobang:",
    "killed": ":octagonal_sign:",
}


class NotificationMessage(pyd.BaseModel):
    content: str
    embeds: list[dict] | None = None
    username: str = "Nexus"


def _get_notification_secrets_from_job(job: schemas.Job) -> tuple[str, str]:
    webhook_url = job.env.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise exc.NotificationError("Missing DISCORD_WEBHOOK_URL in job environment")

    user_id = job.env.get("DISCORD_USER_ID")
    if not user_id:
        raise exc.NotificationError("Missing DISCORD_USER_ID in job environment")

    return webhook_url, user_id


def _format_job_message_for_notification(job: schemas.Job, job_action: JobAction) -> dict:
    discord_id = _get_notification_secrets_from_job(job)[1]
    user_mention = f"<@{discord_id}>"
    message_title = (
        f"{EMOJI_MAPPING[job_action]} - **Job {job.id} {job_action} on GPUs {job.gpu_idxs}** - {user_mention}"
    )
    command = job.command
    git_info = f"{job.git_tag} ({job.git_repo_url}) - Branch: {job.git_branch}"
    gpu_idxs = str(job.gpu_idxs)
    node_name = job.node_name
    wandb_url = "Pending ..." if job_action == "started" and not job.wandb_url else (job.wandb_url or "Not Found")
    fields = [
        {"name": "Command", "value": command},
        {"name": "W&B", "value": wandb_url},
        {"name": "Git", "value": git_info},
        {"name": "User", "value": job.user, "inline": True},
        {"name": "GPUs", "value": gpu_idxs, "inline": True},
        {"name": "Node", "value": node_name, "inline": True},
    ]
    if job.error_message and job_action in ["completed", "failed"]:
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


@exc.handle_exception_async(pyd.ValidationError, exc.NotificationError, message="Invalid notification message format")
@exc.handle_exception_async(aiohttp.ClientError, exc.NotificationError, message="Discord notification request failed")
@exc.handle_exception_async(
    json.JSONDecodeError,
    exc.NotificationError,
    message="Invalid JSON response from Discord notification",
)
async def _send_notification(
    _logger: logger.NexusServerLogger, webhook_url: str, message_data: dict, wait: bool = False
) -> str | None:
    notification_data = NotificationMessage(**message_data)
    params = {"wait": "true"} if wait else {}

    async with aiohttp.ClientSession() as session:
        async with session.post(webhook_url, json=notification_data.model_dump(), params=params) as response:
            if response.status == 204 or response.status == 200:
                if wait:
                    data = await response.json()
                    return data.get("id")
                return None
            else:
                error_msg = f"Failed to send notification: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)


@exc.handle_exception_async(pyd.ValidationError, exc.NotificationError, message="Invalid notification message format")
@exc.handle_exception_async(
    aiohttp.ClientError, exc.NotificationError, message="Discord notification edit request failed"
)
async def _edit_notification_message(
    _logger: logger.NexusServerLogger, notification_url: str, message_id: str, message_data: dict
) -> bool:
    edit_url = f"{notification_url}/messages/{message_id}"
    notification_data = NotificationMessage(**message_data)

    async with aiohttp.ClientSession() as session:
        async with session.patch(edit_url, json=notification_data.model_dump()) as response:
            if response.status != 200:
                error_msg = f"Failed to edit notification: Status {response.status}, Message: {await response.text()}"
                _logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)
            return True


####################


async def notify_job_action(_logger: logger.NexusServerLogger, job: schemas.Job, action: JobAction) -> schemas.Job:
    message_data = _format_job_message_for_notification(job, action)

    webhook_url = _get_notification_secrets_from_job(job)[0]

    if (action == "failed" or action == "killed") and job.dir:
        job_logs = await async_get_job_logs(_logger, job_dir=job.dir, last_n_lines=20)
        if job_logs:
            message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": f"```\n{job_logs}\n```"})

    if action == "started":
        message_id = await _send_notification(_logger, webhook_url=webhook_url, message_data=message_data, wait=True)
        if message_id:
            updated_messages = dict(job.notification_messages)
            updated_messages["discord_start_job"] = message_id
            return dc.replace(job, notification_messages=updated_messages)
        return job
    else:
        await _send_notification(_logger, webhook_url=webhook_url, message_data=message_data)
        return job


async def update_notification_with_wandb(_logger: logger.NexusServerLogger, job: schemas.Job) -> None:
    webhook_url = _get_notification_secrets_from_job(job)[0]

    notification_id = job.notification_messages.get("discord_start_job")

    if not job.wandb_url or not notification_id:
        raise exc.NotificationError("No Discord start job message id found")

    message_data = _format_job_message_for_notification(job, "started")
    await _edit_notification_message(_logger, webhook_url, notification_id, message_data)
    _logger.info(f"Updated notification message for job {job.id} with W&B URL")
