import dataclasses as dc
import datetime
import json
import typing as tp
import urllib.parse

import aiohttp
import pydantic as pyd

from nexus.server.core import exceptions as exc
from nexus.server.core import job, logger, schemas
from nexus.server.integrations import nullpointer

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


def _get_discord_secrets(job: schemas.Job) -> tuple[str, str]:
    webhook_url = job.env.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise exc.NotificationError("Missing DISCORD_WEBHOOK_URL in job environment")

    user_id = job.env.get("DISCORD_USER_ID")
    if not user_id:
        raise exc.NotificationError("Missing DISCORD_USER_ID in job environment")

    return webhook_url, user_id


def _get_whatsapp_secrets(job: schemas.Job) -> tuple[str, str]:
    phone_number = job.env.get("WHATSAPP_TO_NUMBER")
    if not phone_number:
        raise exc.NotificationError("Missing WHATSAPP_TO_NUMBER in job environment")

    api_key = job.env.get("TEXTMEBOT_API_KEY")
    if not api_key:
        raise exc.NotificationError("Missing TEXTMEBOT_API_KEY in job environment")

    return phone_number, api_key


def _format_job_message_for_notification(job: schemas.Job, job_action: JobAction) -> dict:
    # Color mapping for different job statuses
    color_mapping = {
        "started": 0x3498DB,  # Blue
        "completed": 0x2ECC71,  # Green
        "failed": 0xE74C3C,  # Red
        "killed": 0xF39C12,  # Orange/Yellow
    }

    discord_id = _get_discord_secrets(job)[1]
    user_mention = f"<@{discord_id}>"
    gpu_idxs = ", ".join(str(idx) for idx in job.gpu_idxs)
    message_title = f"{EMOJI_MAPPING[job_action]} **Job {job.id} {job_action} on GPU {gpu_idxs} - ({job.node_name})** - {user_mention}"
    command = job.command
    git_info = f"{job.git_tag} ({job.git_repo_url}) - Branch: {job.git_branch}"
    wandb_url = "Pending ..." if job_action == "started" and not job.wandb_url else (job.wandb_url or "Not Found")
    fields = [
        {"name": "Command", "value": command},
        {"name": "W&B", "value": wandb_url},
        {"name": "Git", "value": git_info},
        {"name": "User", "value": job.user, "inline": True},
    ]
    if job.error_message and job_action in ["completed", "failed"]:
        fields.insert(1, {"name": "Error Message", "value": job.error_message})
    return {
        "content": message_title,
        "embeds": [
            {
                "fields": fields,
                "color": color_mapping.get(job_action, 0x4915310),  # Default to original purple if action not found
                "footer": {"text": f"Job Status Update • {job.id}"},
                "timestamp": datetime.datetime.now().isoformat(),
            }
        ],
    }


@exc.handle_exception_async(
    aiohttp.ClientError, exc.NotificationError, message="Discord notification request failed", reraise=False
)
@exc.handle_exception_async(
    json.JSONDecodeError,
    exc.NotificationError,
    message="Invalid JSON response from Discord notification",
    reraise=False,
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


@exc.handle_exception_async(
    aiohttp.ClientError, exc.NotificationError, message="Discord notification edit request failed", reraise=False
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


async def _upload_logs_to_nullpointer(_logger: logger.NexusServerLogger, _job: schemas.Job) -> str | None:
    if not _job.dir:
        return None

    job_logs = await job.async_get_job_logs(_logger, job_dir=_job.dir)
    if not job_logs:
        return None

    instance_url = _job.env.get("NULLPOINTER_URL", "https://0x0.st/")

    # Use our new nullpointer implementation - just upload the raw logs
    paste_url = await nullpointer.upload_text_to_nullpointer(_logger, job_logs, instance_url)

    if paste_url:
        _logger.info(f"Uploaded job logs for {_job.id} to 0x0.st: {paste_url}")

    return paste_url


@exc.handle_exception_async(
    aiohttp.ClientError, exc.NotificationError, message="WhatsApp message failed", reraise=False
)
async def _send_whatsapp_message(
    _logger: logger.NexusServerLogger, phone_number: str, api_key: str, message: str
) -> str:
    # Ensure proper phone number format
    phone_number = phone_number.lstrip("+")

    # URL encode the message
    encoded_message = urllib.parse.quote(message)

    # Construct the API URL
    url = f"https://api.textmebot.com/send.php?recipient={phone_number}&apikey={api_key}&text={encoded_message}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                error_msg = f"Failed to send WhatsApp message: Status {response.status}, Message: {error_text}"
                _logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)

            response_text = await response.text()
            _logger.debug(f"TextMeBot API response: {response_text}")
            return "sent"


async def _send_whatsapp_notification(_logger: logger.NexusServerLogger, job: schemas.Job, message: str) -> None:
    phone_number, api_key = _get_whatsapp_secrets(job)

    result = await _send_whatsapp_message(_logger, phone_number=phone_number, api_key=api_key, message=message)

    _logger.info(f"Sent WhatsApp notification for job {job.id}: {result}")


def _create_message_for_messaging(
    job: schemas.Job, action: JobAction, include_wandb: bool = False, use_whatsapp_format: bool = False
) -> str:
    status_emoji = {"started": "🚀", "completed": "✅", "failed": "❌", "killed": "🛑"}
    emoji = status_emoji.get(action, "")
    gpu_idxs = ", ".join(str(idx) for idx in job.gpu_idxs)

    # WhatsApp formatting:
    # *bold* for bold text
    # _italic_ for italic text
    # ~strikethrough~ for strikethrough
    # ```monospace``` for monospace

    if use_whatsapp_format:
        message_parts = [
            f"{emoji} *Nexus Job {job.id} {action}* on GPU {gpu_idxs} - ({job.node_name})",
            f"*Command:* {job.command}",
            f"*Git:* {job.git_tag} ({job.git_repo_url}) - Branch: {job.git_branch}",
            f"*User:* {job.user}",
        ]

        if include_wandb:
            message_parts.insert(2, f"*W&B:* {job.wandb_url or 'Not Found'}")

        if job.error_message and action in ["completed", "failed"]:
            message_parts.insert(2, f"*Error:* {job.error_message}")
    else:
        message_parts = [
            f"{emoji} Nexus Job {job.id} {action} on GPU {gpu_idxs} - ({job.node_name})",
            f"Command: {job.command}",
            f"Git: {job.git_tag} ({job.git_repo_url}) - Branch: {job.git_branch}",
            f"User: {job.user}",
        ]

        if include_wandb:
            message_parts.insert(2, f"W&B: {job.wandb_url or 'Not Found'}")

        if job.error_message and action in ["completed", "failed"]:
            message_parts.insert(2, f"Error: {job.error_message}")

    return "\n".join(message_parts)


####################


async def notify_job_action(_logger: logger.NexusServerLogger, _job: schemas.Job, action: JobAction) -> schemas.Job:
    updated_job = _job

    if "discord" in _job.notifications:
        message_data = _format_job_message_for_notification(_job, action)
        webhook_url = _get_discord_secrets(_job)[0]

        if action in ["completed", "failed", "killed"] and _job.dir:
            # Show inline logs for failed and killed jobs
            if action in ["failed", "killed"]:
                job_logs = await job.async_get_job_logs(_logger, job_dir=_job.dir, last_n_lines=20)
                if job_logs:
                    message_data["embeds"][0]["fields"].append(
                        {"name": "Last few log lines", "value": f"```\n{job_logs}\n```"}
                    )

            # Always upload full logs for completed, failed, and killed jobs
            logs_url = await _upload_logs_to_nullpointer(_logger, _job)
            if logs_url:
                _logger.info(f"Adding logs URL to Discord message: {logs_url}")
                message_data["embeds"][0]["fields"].append(
                    {"name": "Full logs", "value": f"[View full logs]({logs_url})"}
                )

        if action == "started":
            message_id = await _send_notification(
                _logger, webhook_url=webhook_url, message_data=message_data, wait=True
            )
            if message_id:
                updated_messages = dict(_job.notification_messages)
                updated_messages["discord_start_job"] = message_id
                updated_job = dc.replace(updated_job, notification_messages=updated_messages)
        else:
            await _send_notification(_logger, webhook_url=webhook_url, message_data=message_data)

    if "whatsapp" in _job.notifications:
        messaging_text = _create_message_for_messaging(_job, action, include_wandb=False, use_whatsapp_format=True)

        if action in ["completed", "failed", "killed"] and _job.dir:
            # Add error logs for failed and killed jobs
            if action in ["failed", "killed"]:
                job_logs = await job.async_get_job_logs(_logger, job_dir=_job.dir, last_n_lines=10)
                if job_logs:
                    messaging_text += f"\n\n*Last few log lines:*\n```{job_logs}```"

            # Always add logs URL for completed, failed, and killed jobs
            logs_url = await _upload_logs_to_nullpointer(_logger, _job)
            if logs_url:
                messaging_text += f"\n\n*Full logs:* {logs_url}"

        await _send_whatsapp_notification(_logger, _job, messaging_text)

    return updated_job


async def update_notification_with_wandb(_logger: logger.NexusServerLogger, job: schemas.Job) -> None:
    if "discord" in job.notifications:
        webhook_url = _get_discord_secrets(job)[0]

        notification_id = job.notification_messages.get("discord_start_job")

        if not job.wandb_url or not notification_id:
            raise exc.NotificationError("No Discord start job message id found")

        message_data = _format_job_message_for_notification(job, "started")
        await _edit_notification_message(_logger, webhook_url, notification_id, message_data)
        _logger.info(f"Updated notification message for job {job.id} with W&B URL")
