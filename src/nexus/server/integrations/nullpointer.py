import aiohttp

from nexus.server.core import exceptions as exc
from nexus.server.core import logger


@exc.handle_exception_async(
    Exception, exc.NotificationError, message="0x0.st upload failed", reraise=False, default_return=None
)
async def upload_text_to_nullpointer(
    _logger: logger.NexusServerLogger, text: str, instance_url: str = "https://0x0.st/"
) -> str | None:
    if not text:
        return None

    form_data = aiohttp.FormData()
    form_data.add_field("file", text.encode("utf-8"), filename="job_logs.txt", content_type="text/plain")

    async with aiohttp.ClientSession() as session:
        async with session.post(instance_url, data=form_data) as response:
            if response.status == 200:
                url = await response.text()
                url = url.strip()
                return url
            else:
                error_text = await response.text()
                _logger.error(f"0x0.st upload failed with status {response.status}: {error_text}")

    return None
