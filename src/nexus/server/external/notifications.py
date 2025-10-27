import dataclasses as dc
import datetime as dt
import json
import typing as tp

import aiohttp
import pydantic as pyd

from nexus.server.core import exceptions as exc
from nexus.server.core import job, schemas
from nexus.server.external import nullpointer
from nexus.server.utils import logger

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


def _get_phone_secrets(job: schemas.Job) -> tuple[str, str, str, str]:
    phone_number = job.env.get("PHONE_TO_NUMBER")
    if not phone_number:
        raise exc.NotificationError("Missing PHONE_TO_NUMBER in job environment")

    twilio_account_sid = job.env.get("TWILIO_ACCOUNT_SID")
    if not twilio_account_sid:
        raise exc.NotificationError("Missing TWILIO_ACCOUNT_SID in job environment")

    twilio_auth_token = job.env.get("TWILIO_AUTH_TOKEN")
    if not twilio_auth_token:
        raise exc.NotificationError("Missing TWILIO_AUTH_TOKEN in job environment")

    twilio_from_number = job.env.get("TWILIO_FROM_NUMBER")
    if not twilio_from_number:
        raise exc.NotificationError("Missing TWILIO_FROM_NUMBER in job environment")

    return phone_number, twilio_account_sid, twilio_auth_token, twilio_from_number


def _truncate_field_value(value: str, max_length: int = 1024) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def _format_job_message_for_notification(job: schemas.Job, job_action: JobAction) -> dict:
    color_mapping = {
        "started": 0x3498DB,
        "completed": 0x2ECC71,
        "failed": 0xE74C3C,
        "killed": 0xF39C12,
    }
    discord_id = _get_discord_secrets(job)[1]
    user_mention = f"<@{discord_id}>"
    gpu_idxs = ", ".join(str(idx) for idx in job.gpu_idxs) if job.gpu_idxs else "None"
    message_title = f"{EMOJI_MAPPING[job_action]} **Job {job.id} {job_action} on GPU(s) {gpu_idxs} - ({job.node_name})** - {user_mention}"
    command = _truncate_field_value(str(job.command))
    if job.git_tag:
        git_info = _truncate_field_value(f"{job.git_tag} ({job.git_repo_url}) - Branch: {job.git_branch}")
    else:
        git_info = _truncate_field_value(f"({job.git_repo_url}) - Branch: {job.git_branch}")
    fields = [
        {"name": "Command", "value": command},
        {"name": "Git", "value": git_info},
        {"name": "User", "value": str(job.user), "inline": True},
    ]
    if job.error_message and job_action in ["completed", "failed"]:
        fields.insert(1, {"name": "Error Message", "value": _truncate_field_value(str(job.error_message))})
    return {
        "content": message_title,
        "embeds": [
            {
                "fields": fields,
                "color": color_mapping.get(job_action, 0x4915310),
                "footer": {"text": f"Job Status Update • {job.id}"},
                "timestamp": dt.datetime.now().isoformat(),
            }
        ],
    }


@exc.handle_exception(
    aiohttp.ClientError, exc.NotificationError, message="Discord notification request failed", reraise=False
)
@exc.handle_exception(
    json.JSONDecodeError,
    exc.NotificationError,
    message="Invalid JSON response from Discord notification",
    reraise=False,
)
async def _send_notification(webhook_url: str, message_data: dict, wait: bool = False) -> str | None:
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
                logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)


@exc.handle_exception(aiohttp.ClientError, exc.NotificationError, message="Discord notification edit request failed")
async def _edit_notification_message(notification_url: str, message_id: str, message_data: dict) -> bool:
    edit_url = f"{notification_url}/messages/{message_id}"
    notification_data = NotificationMessage(**message_data)

    async with aiohttp.ClientSession() as session:
        async with session.patch(edit_url, json=notification_data.model_dump()) as response:
            if response.status != 200:
                error_msg = f"Failed to edit notification: Status {response.status}, Message: {await response.text()}"
                logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)
            return True


async def _upload_logs_to_nullpointer(_job: schemas.Job) -> str | None:
    if "nullpointer" not in _job.integrations or not _job.dir:
        return None

    job_logs = await job.async_get_job_logs(_job.dir)
    if not job_logs:
        return None

    paste_url = await nullpointer.upload_text_to_nullpointer(job_logs)

    if paste_url:
        logger.info(f"Uploaded job logs for {_job.id} to 0x0.st: {paste_url}")

    return paste_url


@exc.handle_exception(aiohttp.ClientError, exc.NotificationError, message="Phone call notification failed")
async def _make_phone_call(to_number: str, from_number: str, account_sid: str, auth_token: str, message: str) -> str:
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">{message}</Say>
    <Pause length="1"/>
    <Say voice="alice">This was a notification from Nexus. Goodbye.</Say>
</Response>"""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

    auth = aiohttp.BasicAuth(account_sid, auth_token)
    data = {"To": to_number, "From": from_number, "Twiml": twiml}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, auth=auth, data=data) as response:
            response_text = await response.text()

            if response.status not in [200, 201]:
                error_msg = f"Failed to make phone call: Status {response.status}, Message: {response_text}"
                logger.error(error_msg)
                raise exc.NotificationError(message=error_msg)

            logger.debug(f"Twilio API response: {response_text}")
            return "call_initiated"


async def _send_phone_notification(job: schemas.Job, job_action: JobAction) -> None:
    if job_action not in ["completed", "failed", "killed"]:
        return

    to_number, account_sid, auth_token, from_number = _get_phone_secrets(job)

    status = "completed successfully" if job_action == "completed" else "failed"
    message = f"Your Nexus job {job.id} has {status}. The command was: {job.command}."

    result = await _make_phone_call(
        to_number,
        from_number=from_number,
        account_sid=account_sid,
        auth_token=auth_token,
        message=message,
    )

    logger.info(f"Initiated phone call notification for job {job.id}: {result}")


####################


@exc.handle_exception(Exception, reraise=False, default_return=exc.RETURN_FIRST_ARG)
async def notify_job_action(_job: schemas.Job, action: JobAction) -> schemas.Job:
    updated_job = _job

    if "discord" in _job.notifications:
        message_data = _format_job_message_for_notification(_job, action)
        webhook_url = _get_discord_secrets(_job)[0]

        if action in ["completed", "failed", "killed"] and _job.dir:
            if action in ["failed", "killed"]:
                job_logs = await job.async_get_job_logs(_job.dir, last_n_lines=20)
                if job_logs:
                    MAX_FIELD_LENGTH = 1024
                    log_field = f"```\n{job_logs}\n```"
                    if len(log_field) > MAX_FIELD_LENGTH:
                        allowed_length = MAX_FIELD_LENGTH - len("```\n...\n```")
                        truncated_logs = job_logs[:allowed_length] + "..."
                        log_field = f"```\n{truncated_logs}\n```"
                    message_data["embeds"][0]["fields"].append({"name": "Last few log lines", "value": log_field})

            logs_url = await _upload_logs_to_nullpointer(_job)
            if logs_url:
                logger.info(f"Adding logs URL to Discord message: {logs_url}")
                message_data["embeds"][0]["fields"].append(
                    {"name": "Full logs", "value": f"[View full logs]({logs_url})"}
                )

        if action == "started":
            message_id = await _send_notification(webhook_url, message_data=message_data, wait=True)
            if message_id:
                updated_messages = dict(_job.notification_messages)
                updated_messages["discord_start_job"] = message_id
                updated_job = dc.replace(updated_job, notification_messages=updated_messages)
        else:
            await _send_notification(webhook_url, message_data=message_data)

    if "phone" in _job.notifications:
        await _send_phone_notification(_job, action)

    return updated_job


@exc.handle_exception(Exception, reraise=False)
async def update_notification_with_wandb(job: schemas.Job) -> None:
    if "discord" in job.notifications:
        webhook_url = _get_discord_secrets(job)[0]

        notification_id = job.notification_messages.get("discord_start_job")

        if not job.wandb_url or not notification_id:
            raise exc.NotificationError("No Discord start job message id found")

        message_data = _format_job_message_for_notification(job, "started")
        await _edit_notification_message(webhook_url, notification_id, message_data)
        logger.info(f"Updated notification message for job {job.id} with W&B URL")
