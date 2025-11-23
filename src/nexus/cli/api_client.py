import functools
import json
import time
import typing as tp

import requests
from termcolor import colored

from nexus.cli import config, tunnel_manager
from nexus.cli.tunnel_manager import SSHTunnelError


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
        max_retries = 2
        last_error = None

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except SSHTunnelError as e:
                print(colored("\nSSH Tunnel Error:", "red", attrs=["bold"]))
                print(str(e))
                raise
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_error = e
                if attempt < max_retries - 1:
                    target_name = kwargs.get("target_name")
                    active_name, target_cfg = config.get_active_target(target_name)
                    if target_cfg and target_cfg.host not in ("localhost", "127.0.0.1"):
                        print(
                            colored(
                                f"\nConnection failed, recreating tunnel (attempt {attempt + 1}/{max_retries})...",
                                "yellow",
                            )
                        )
                        tunnel_manager._stop_control_master(active_name)
                        time.sleep(0.5)
                        continue
                print(colored("\nConnection Error:", "red", attrs=["bold"]))
                print(f"Failed to connect after {max_retries} attempts: {last_error}")
                raise
            except requests.exceptions.HTTPError as e:
                _print_error_response(e.response)
                raise

        raise last_error

    return wrapper


def get_api_base_url(target_name: str | None = None) -> str:
    active_name, target_cfg = config.get_active_target(target_name)

    if target_cfg is None or target_cfg.host in ("localhost", "127.0.0.1"):
        port = target_cfg.port if target_cfg else 54323
        return f"http://127.0.0.1:{port}/v1"

    local_port = tunnel_manager.get_or_create_tunnel(active_name)
    return f"http://127.0.0.1:{local_port}/v1"


def check_api_connection(target_name: str | None = None) -> bool:
    try:
        api_url = get_api_base_url(target_name)
        response = requests.get(f"{api_url}/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


@handle_api_errors
def get_gpus(target_name: str | None = None) -> list[dict]:
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/gpus", timeout=5)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_jobs(status: str | None = None, target_name: str | None = None) -> list[dict]:
    params = {"status": status} if status else {}
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/jobs", params=params, timeout=5)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job(job_id: str, target_name: str | None = None) -> dict:
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/jobs/{job_id}", timeout=5)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job_logs(job_id: str, last_n_lines: int | None = None, target_name: str | None = None) -> str:
    params = {}
    if last_n_lines is not None:
        params["last_n_lines"] = last_n_lines
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/jobs/{job_id}/logs", params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if "data" not in data:
        raise ValueError(f"API response missing 'data' field: {data}")
    return data["data"]


@handle_api_errors
def get_server_status(target_name: str | None = None) -> dict:
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/server/status", timeout=10)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_detailed_health(refresh: bool = False, target_name: str | None = None) -> dict:
    params = {"detailed": True}
    if refresh:
        params["refresh"] = True
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/health", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def check_artifact_by_sha(git_sha: str, target_name: str | None = None) -> tuple[bool, str | None]:
    api_url = get_api_base_url(target_name)
    response = requests.get(f"{api_url}/artifacts/by-sha/{git_sha}", timeout=5)
    response.raise_for_status()
    result = response.json()
    return result["exists"], result.get("artifact_id")


@handle_api_errors
def upload_artifact(data: bytes, git_sha: str | None = None, target_name: str | None = None) -> str:
    headers = {"Content-Type": "application/octet-stream"}
    params = {}
    if git_sha:
        params["git_sha"] = git_sha
    api_url = get_api_base_url(target_name)
    response = requests.post(f"{api_url}/artifacts", data=data, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    result = response.json()
    if "data" not in result:
        raise ValueError(f"API response missing 'data' field: {result}")
    return result["data"]


@handle_api_errors
def add_job(job_request: dict, target_name: str | None = None) -> dict:
    api_url = get_api_base_url(target_name)
    response = requests.post(f"{api_url}/jobs", json=job_request, timeout=10)
    response.raise_for_status()
    return response.json()


def _process_job_batch(
    job_ids: list[str],
    method: tp.Literal["POST", "DELETE"],
    endpoint_suffix: str,
    success_key: str,
    target_name: str | None,
) -> dict:
    results = {success_key: [], "failed": []}
    api_url = get_api_base_url(target_name)
    request_fn = requests.post if method == "POST" else requests.delete
    for job_id in job_ids:
        try:
            response = request_fn(f"{api_url}/jobs/{job_id}{endpoint_suffix}", timeout=5)
            if response.status_code == 204:
                results[success_key].append(job_id)
            else:
                response.raise_for_status()
        except Exception as e:
            results["failed"].append({"id": job_id, "error": str(e)})
    return results


@handle_api_errors
def kill_running_jobs(job_ids: list[str], target_name: str | None = None) -> dict:
    return _process_job_batch(job_ids, "POST", "/kill", "killed", target_name)


@handle_api_errors
def remove_queued_jobs(job_ids: list[str], target_name: str | None = None) -> dict:
    return _process_job_batch(job_ids, "DELETE", "", "removed", target_name)


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

    api_url = get_api_base_url(target_name)
    response = requests.patch(f"{api_url}/jobs/{job_id}", json=update_data, timeout=5)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def manage_blacklist(
    gpu_indices: list[int], action: tp.Literal["add", "remove"], target_name: str | None = None
) -> dict:
    results = {"blacklisted": [], "removed": [], "failed": []}
    api_url = get_api_base_url(target_name)

    for gpu_idx in gpu_indices:
        try:
            if action == "add":
                response = requests.put(f"{api_url}/gpus/{gpu_idx}/blacklist", timeout=5)
                if response.ok:
                    results["blacklisted"].append(gpu_idx)
            else:
                response = requests.delete(f"{api_url}/gpus/{gpu_idx}/blacklist", timeout=5)
                if response.ok:
                    results["removed"].append(gpu_idx)

            response.raise_for_status()
        except Exception as e:
            results["failed"].append({"index": gpu_idx, "error": str(e)})

    return results
