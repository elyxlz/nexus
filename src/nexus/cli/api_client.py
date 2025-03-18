import functools
import json
import typing as tp

import requests
from termcolor import colored

from nexus.cli import config


def _print_error_response(response):
    """Print a formatted error response"""
    print(colored("\nAPI Error Response:", "red", attrs=["bold"]))

    try:
        error_data = json.loads(response.text)

        # Handle validation errors (422)
        if response.status_code == 422 and "detail" in error_data:
            for error in error_data["detail"]:
                # Extract field name from loc if available
                field = error.get("loc", [])[-1] if error.get("loc") else ""
                field_str = f" ({field})" if field and field != "body" else ""

                # Get the error message
                msg = error.get("msg", "Unknown validation error")

                print(f"  {colored('•', 'red')} {msg}{field_str}")

                # For debugging complex validation errors
                if "ctx" in error and "error" in error["ctx"]:
                    ctx_error = error["ctx"]["error"]
                    if ctx_error:
                        print(f"    {colored('Details:', 'yellow')} {ctx_error}")

        # Handle custom API errors with message
        elif "message" in error_data:
            print(f"  {colored('•', 'red')} {error_data['message']}")
            if "error" in error_data:
                print(f"    Error code: {error_data['error']}")

        # Fallback for other JSON responses
        else:
            print(f"  {colored('•', 'red')} {json.dumps(error_data, indent=2)}")

    except (json.JSONDecodeError, ValueError):
        # Fallback for non-JSON responses
        print(f"  {colored('•', 'red')} {response.text}")


def handle_api_errors(func):
    """Decorator to handle API errors and display nicely formatted responses"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            _print_error_response(e.response)
            raise

    return wrapper


def get_api_base_url() -> str:
    cfg = config.load_config()
    return f"http://localhost:{cfg.port}/v1"


def check_api_connection() -> bool:
    cfg = config.load_config()
    try:
        response = requests.get(f"http://localhost:{cfg.port}/v1/health", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


@handle_api_errors
def get_gpus() -> list[dict]:
    response = requests.get(f"{get_api_base_url()}/gpus")
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_jobs(status: str | None = None) -> list[dict]:
    params = {"status": status} if status else {}
    response = requests.get(f"{get_api_base_url()}/jobs", params=params)
    response.raise_for_status()
    return response.json()


# We'll now use get_jobs("queued") instead of a separate get_queue function


@handle_api_errors
def get_job(job_id: str) -> dict:
    response = requests.get(f"{get_api_base_url()}/jobs/{job_id}")
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job_logs(job_id: str, last_n_lines: int | None = None) -> str:
    params = {}
    if last_n_lines is not None:
        params["last_n_lines"] = last_n_lines
    response = requests.get(f"{get_api_base_url()}/jobs/{job_id}/logs", params=params)
    response.raise_for_status()
    return response.json().get("logs", "")


@handle_api_errors
def get_server_status() -> dict:
    response = requests.get(f"{get_api_base_url()}/server/status")
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_detailed_health(refresh: bool = False) -> dict:
    params = {"detailed": True}
    if refresh:
        params["refresh"] = True
    response = requests.get(f"{get_api_base_url()}/health", params=params)
    response.raise_for_status()
    return response.json()


def check_heartbeat() -> bool:
    try:
        response = requests.get(f"{get_api_base_url()}/health", timeout=1)
        return response.status_code == 200
    except requests.RequestException:
        return False


@handle_api_errors
def add_job(job_request: dict) -> dict:
    response = requests.post(f"{get_api_base_url()}/jobs", json=job_request)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def kill_running_jobs(job_ids: list[str]) -> dict:
    # In the new API, we need to make individual kill requests per job
    results = {"killed": [], "failed": []}

    for job_id in job_ids:
        try:
            response = requests.post(f"{get_api_base_url()}/jobs/{job_id}/kill")
            if response.status_code == 204:
                results["killed"].append(job_id)
            else:
                response.raise_for_status()  # Will raise an exception for other errors
        except Exception as e:
            results["failed"].append({"id": job_id, "error": str(e)})

    return results


@handle_api_errors
def remove_queued_jobs(job_ids: list[str]) -> dict:
    # In the new API, we need to make individual delete requests per job
    results = {"removed": [], "failed": []}

    for job_id in job_ids:
        try:
            response = requests.delete(f"{get_api_base_url()}/jobs/{job_id}")
            if response.status_code == 204:
                results["removed"].append(job_id)
            else:
                response.raise_for_status()  # Will raise an exception for other errors
        except Exception as e:
            results["failed"].append({"id": job_id, "error": str(e)})

    return results


@handle_api_errors
def edit_job(job_id: str, command: str | None = None, priority: int | None = None) -> dict:
    update_data = {}
    if command is not None:
        update_data["command"] = command
    if priority is not None:
        update_data["priority"] = priority

    response = requests.patch(f"{get_api_base_url()}/jobs/{job_id}", json=update_data)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def manage_blacklist(gpu_indices: list[int], action: tp.Literal["add", "remove"]) -> dict:
    # In the new API, we need to make individual blacklist requests per GPU
    results = {"blacklisted": [], "removed": [], "failed": []}

    for gpu_idx in gpu_indices:
        try:
            if action == "add":
                response = requests.put(f"{get_api_base_url()}/gpus/{gpu_idx}/blacklist")
                if response.ok:
                    results["blacklisted"].append(gpu_idx)
            else:
                response = requests.delete(f"{get_api_base_url()}/gpus/{gpu_idx}/blacklist")
                if response.ok:
                    results["removed"].append(gpu_idx)

            response.raise_for_status()
        except Exception as e:
            results["failed"].append({"index": gpu_idx, "error": str(e)})

    return results
