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
    return f"http://{cfg.host}:{cfg.port}/v1"


def check_api_connection() -> bool:
    cfg = config.load_config()
    try:
        response = requests.get(f"http://{cfg.host}:{cfg.port}/v1/heartbeat", timeout=2)
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


@handle_api_errors
def get_queue() -> list[dict]:
    response = requests.get(f"{get_api_base_url()}/queue")
    response.raise_for_status()
    return response.json()


@handle_api_errors
def get_job_logs(job_id: str) -> str:
    response = requests.get(f"{get_api_base_url()}/jobs/{job_id}/logs")
    response.raise_for_status()
    return response.json().get("logs", "")


@handle_api_errors
def get_server_status() -> dict:
    response = requests.get(f"{get_api_base_url()}/server/status")
    response.raise_for_status()
    return response.json()


def check_heartbeat() -> bool:
    try:
        response = requests.get(f"{get_api_base_url()}/heartbeat", timeout=1)
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
    response = requests.delete(f"{get_api_base_url()}/jobs/running", json=job_ids)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def remove_queued_jobs(job_ids: list[str]) -> dict:
    response = requests.delete(f"{get_api_base_url()}/jobs/queued", json=job_ids)
    response.raise_for_status()
    return response.json()


@handle_api_errors
def manage_blacklist(gpu_indices: list[int], action: tp.Literal["add", "remove"]) -> dict:
    if action == "add":
        response = requests.post(f"{get_api_base_url()}/gpus/blacklist", json=gpu_indices)
    else:
        response = requests.delete(f"{get_api_base_url()}/gpus/blacklist", json=gpu_indices)

    response.raise_for_status()
    return response.json()
