import functools
import json
import typing as tp

import requests
from termcolor import colored

from nexus.cli import config


def _get_verify(target_name: str | None) -> str | bool:
    active_name, target_cfg = config.get_active_target(target_name)

    if target_cfg is None:
        return True

    if target_cfg.protocol == "http":
        return True

    cert = config.get_server_cert_path(target_cfg.host, target_cfg.port)
    if not cert.exists():
        raise FileNotFoundError(f"SSL certificate not found at {cert}. Run 'nx targets add' to reconfigure.")
    return str(cert)


def _print_error_response(response):
    print(colored("\nAPI Error Response:", "red", attrs=["bold"]))

    try:
        error_data = json.loads(response.text)

        if response.status_code == 422 and "detail" in error_data:
            for error in error_data["detail"]:
                field = error.get("loc", [])[-1] if error.get("loc") else ""
                field_str = f" ({field})" if field and field != "body" else ""
                msg = error.get("msg", "Unknown validation error")
                print(f"  {colored('•', 'red')} {msg}{field_str}")

                if "ctx" in error and "error" in error["ctx"]:
                    ctx_error = error["ctx"]["error"]
                    if ctx_error:
                        print(f"    {colored('Details:', 'yellow')} {ctx_error}")

        elif "message" in error_data:
            print(f"  {colored('•', 'red')} {error_data['message']}")
            if "error" in error_data:
                print(f"    Error code: {error_data['error']}")

        else:
            print(f"  {colored('•', 'red')} {json.dumps(error_data, indent=2)}")

    except (json.JSONDecodeError, ValueError):
        print(f"  {colored('•', 'red')} {response.text}")


def handle_api_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            _print_error_response(e.response)
            raise

    return wrapper


def get_api_base_url(target_name: str | None) -> str:
    _, target_cfg = config.get_active_target(target_name)

    if target_cfg is None:
        return "http://localhost:54323/v1"

    return f"{target_cfg.protocol}://{target_cfg.host}:{target_cfg.port}/v1"


def _get_headers(target_name: str | None) -> dict[str, str]:
    _, target_cfg = config.get_active_target(target_name)
    if target_cfg and target_cfg.api_token:
        return {"Authorization": f"Bearer {target_cfg.api_token}"}
    return {}


def check_api_connection(target_name: str | None = None) -> bool:
    try:
        url = f"{get_api_base_url(target_name)}/health"
        response = requests.get(url, headers=_get_headers(target_name), timeout=2, verify=_get_verify(target_name))
        return response.status_code == 200
    except requests.RequestException:
        return False


@handle_api_errors
def get_gpus(target_name: str | None = None) -> list[dict]:
    response = requests.get(
        f"{get_api_base_url(target_name)}/gpus", headers=_get_headers(target_name), verify=_get_verify(target_name)
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_jobs(status: str | None = None, target_name: str | None = None) -> list[dict]:
    params = {"status": status} if status else {}
    response = requests.get(
        f"{get_api_base_url(target_name)}/jobs",
        params=params,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job(job_id: str, target_name: str | None = None) -> dict:
    response = requests.get(
        f"{get_api_base_url(target_name)}/jobs/{job_id}",
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job_logs(job_id: str, last_n_lines: int | None = None, target_name: str | None = None) -> str:
    params = {}
    if last_n_lines is not None:
        params["last_n_lines"] = last_n_lines
    response = requests.get(
        f"{get_api_base_url(target_name)}/jobs/{job_id}/logs",
        params=params,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    data = response.json()
    if "data" not in data:
        raise ValueError(f"API response missing 'data' field: {data}")
    return data["data"]


@handle_api_errors
def get_server_status(target_name: str | None = None) -> dict:
    response = requests.get(
        f"{get_api_base_url(target_name)}/server/status",
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_detailed_health(refresh: bool = False, target_name: str | None = None) -> dict:
    params = {"detailed": True}
    if refresh:
        params["refresh"] = True
    response = requests.get(
        f"{get_api_base_url(target_name)}/health",
        params=params,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


def check_heartbeat(target_name: str | None = None) -> bool:
    try:
        response = requests.get(
            f"{get_api_base_url(target_name)}/health",
            headers=_get_headers(target_name),
            timeout=1,
            verify=_get_verify(target_name),
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


@handle_api_errors
def upload_artifact(data: bytes, target_name: str | None = None) -> str:
    headers = _get_headers(target_name)
    headers["Content-Type"] = "application/octet-stream"
    response = requests.post(
        f"{get_api_base_url(target_name)}/artifacts", data=data, headers=headers, verify=_get_verify(target_name)
    )
    response.raise_for_status()
    result = response.json()
    if "data" not in result:
        raise ValueError(f"API response missing 'data' field: {result}")
    return result["data"]


@handle_api_errors
def add_job(job_request: dict, target_name: str | None = None) -> dict:
    response = requests.post(
        f"{get_api_base_url(target_name)}/jobs",
        json=job_request,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def kill_running_jobs(job_ids: list[str], target_name: str | None = None) -> dict:
    results = {"killed": [], "failed": []}

    for job_id in job_ids:
        try:
            response = requests.post(
                f"{get_api_base_url(target_name)}/jobs/{job_id}/kill",
                headers=_get_headers(target_name),
                verify=_get_verify(target_name),
            )
            if response.status_code == 204:
                results["killed"].append(job_id)
            else:
                response.raise_for_status()
        except Exception as e:
            results["failed"].append({"id": job_id, "error": str(e)})

    return results


@handle_api_errors
def remove_queued_jobs(job_ids: list[str], target_name: str | None = None) -> dict:
    results = {"removed": [], "failed": []}

    for job_id in job_ids:
        try:
            response = requests.delete(
                f"{get_api_base_url(target_name)}/jobs/{job_id}",
                headers=_get_headers(target_name),
                verify=_get_verify(target_name),
            )
            if response.status_code == 204:
                results["removed"].append(job_id)
            else:
                response.raise_for_status()
        except Exception as e:
            results["failed"].append({"id": job_id, "error": str(e)})

    return results


@handle_api_errors
def edit_job(
    job_id: str,
    command: str | None = None,
    priority: int | None = None,
    num_gpus: int | None = None,
    git_tag: str | None = None,
    target_name: str | None = None,
) -> dict:
    update_data = {}
    if command is not None:
        update_data["command"] = command
    if priority is not None:
        update_data["priority"] = priority
    if num_gpus is not None:
        update_data["num_gpus"] = num_gpus
    if git_tag is not None:
        update_data["git_tag"] = git_tag

    response = requests.patch(
        f"{get_api_base_url(target_name)}/jobs/{job_id}",
        json=update_data,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()


@handle_api_errors
def manage_blacklist(
    gpu_indices: list[int], action: tp.Literal["add", "remove"], target_name: str | None = None
) -> dict:
    results = {"blacklisted": [], "removed": [], "failed": []}

    for gpu_idx in gpu_indices:
        try:
            if action == "add":
                response = requests.put(
                    f"{get_api_base_url(target_name)}/gpus/{gpu_idx}/blacklist",
                    headers=_get_headers(target_name),
                    verify=_get_verify(target_name),
                )
                if response.ok:
                    results["blacklisted"].append(gpu_idx)
            else:
                response = requests.delete(
                    f"{get_api_base_url(target_name)}/gpus/{gpu_idx}/blacklist",
                    headers=_get_headers(target_name),
                    verify=_get_verify(target_name),
                )
                if response.ok:
                    results["removed"].append(gpu_idx)

            response.raise_for_status()
        except Exception as e:
            results["failed"].append({"index": gpu_idx, "error": str(e)})

    return results


@handle_api_errors
def register_ssh_key(public_key: str, target_name: str | None = None) -> dict:
    response = requests.post(
        f"{get_api_base_url(target_name)}/auth/ssh-key",
        data=public_key,
        headers=_get_headers(target_name),
        verify=_get_verify(target_name),
    )
    response.raise_for_status()
    return response.json()
