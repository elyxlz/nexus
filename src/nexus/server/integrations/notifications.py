import dataclasses as dc
import datetime
import json
import typing as tp

import aiohttp
import pydantic as pyd
import twilio.base.exceptions
import twilio.rest

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


def _get_twilio_secrets(job: schemas.Job) -> tuple[str, str, str, str]:
    account_sid = job.env.get("TWILIO_ACCOUNT_SID")
    if not account_sid:
        raise exc.NotificationError("Missing TWILIO_ACCOUNT_SID in job environment")

    auth_token = job.env.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        raise exc.NotificationError("Missing TWILIO_AUTH_TOKEN in job environment")

    from_number = job.env.get("TWILIO_FROM_NUMBER")
    if not from_number:
        raise exc.NotificationError("Missing TWILIO_FROM_NUMBER in job environment")

    # Get the appropriate destination number based on notification type
    if "whatsapp" in job.notifications:
        to_number = job.env.get("WHATSAPP_TO_NUMBER")
        if not to_number:
            raise exc.NotificationError("Missing WHATSAPP_TO_NUMBER in job environment")
    elif "phone" in job.notifications:
        to_number = job.env.get("PHONE_TO_NUMBER")
        if not to_number:
            raise exc.NotificationError("Missing PHONE_TO_NUMBER in job environment")
    else:
        raise exc.NotificationError("No valid Twilio notification type configured")

    return account_sid, auth_token, from_number, to_number


def _format_job_message_for_notification(job: schemas.Job, job_action: JobAction) -> dict:
    discord_id = _get_discord_secrets(job)[1]
    user_mention = f"<@{discord_id}>"
    gpu_idxs = ", ".join(str(idx) for idx in job.gpu_idxs)
    message_title = f"{EMOJI_MAPPING[job_action]} - **Job {job.id} {job_action} on GPU {gpu_idxs} - ({job.node_name})** - {user_mention}"
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
    twilio.base.exceptions.TwilioRestException, exc.NotificationError, message="Twilio WhatsApp message failed"
)
async def _send_whatsapp_notification(_logger: logger.NexusServerLogger, job: schemas.Job, message: str) -> None:
    account_sid, auth_token, from_number, to_number = _get_twilio_secrets(job)

    whatsapp_from = f"whatsapp:{from_number}"
    whatsapp_to = f"whatsapp:{to_number}"

    client = twilio.rest.Client(account_sid, auth_token)

    result = client.messages.create(body=message, from_=whatsapp_from, to=whatsapp_to)

    _logger.info(f"Sent WhatsApp notification for job {job.id}: {result.sid}")


@exc.handle_exception_async(
    twilio.base.exceptions.TwilioRestException, exc.NotificationError, message="Twilio phone call failed"
)
async def _send_phone_notification(_logger: logger.NexusServerLogger, job: schemas.Job, message: str) -> None:
    account_sid, auth_token, from_number, to_number = _get_twilio_secrets(job)

    client = twilio.rest.Client(account_sid, auth_token)

    twiml = f"""
    <Response>
        <Say>{message}</Say>
        <Pause length="1"/>
        <Say>This call was automated by Nexus. Goodbye.</Say>
    </Response>
    """

    result = client.calls.create(twiml=twiml, from_=from_number, to=to_number)

    _logger.info(f"Initiated phone notification for job {job.id}: {result.sid}")


def _create_message_for_twilio(job: schemas.Job, action: JobAction) -> str:
    """Create a plain text message for Twilio notifications (WhatsApp and phone)."""
    status_emoji = {"started": "🚀", "completed": "✅", "failed": "❌", "killed": "🛑"}
    emoji = status_emoji.get(action, "")

    message_parts = [
        f"{emoji} Nexus Job {job.id} {action} on GPUs {job.gpu_idxs}",
        f"Command: {job.command}",
        f"User: {job.user}",
    ]

    if job.wandb_url:
        message_parts.append(f"Weights & Biases: {job.wandb_url}")

    if job.error_message and action in ["completed", "failed"]:
        message_parts.append(f"Error: {job.error_message}")

    return "\n".join(message_parts)


####################


async def notify_job_action(_logger: logger.NexusServerLogger, _job: schemas.Job, action: JobAction) -> schemas.Job:
    updated_job = _job

    if "discord" in _job.notifications:
        message_data = _format_job_message_for_notification(_job, action)
        webhook_url = _get_discord_secrets(_job)[0]

        if action in ["completed", "failed", "killed"] and _job.dir:
            # Only show inline logs for failed jobs, not successful ones
            if action in ["failed", "killed"]:
                job_logs = await job.async_get_job_logs(_logger, job_dir=_job.dir, last_n_lines=20)
                if job_logs:
                    message_data["embeds"][0]["fields"].append(
                        {"name": "Last few log lines", "value": f"```\n{job_logs}\n```"}
                    )

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
        twilio_message = _create_message_for_twilio(_job, action)
        await _send_whatsapp_notification(_logger, _job, twilio_message)

    if "phone" in _job.notifications:
        if action in ["completed", "failed", "killed"]:
            twilio_message = _create_message_for_twilio(_job, action)
            await _send_phone_notification(_logger, _job, twilio_message)

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
