import hashlib
import itertools
import os
import pathlib as pl
import re
import subprocess
import time
import typing as tp

import base58
from termcolor import colored

# Types
Color = tp.Literal["grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
Attribute = tp.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]


def generate_git_tag_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def get_current_git_branch() -> str:
    try:
        # First check if we're in a git repository
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # If we are, get the branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown-branch"


# Time Utilities
def format_runtime(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def format_timestamp(timestamp: float | None) -> str:
    if not timestamp:
        return "Unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def calculate_runtime(job: dict) -> float:
    if not job.get("started_at"):
        return 0.0
    if job.get("status") in ["completed", "failed", "killed"] and job.get("completed_at"):
        return job["completed_at"] - job["started_at"]
    elif job.get("status") == "running":
        return time.time() - job["started_at"]
    return 0.0


def parse_gpu_list(gpu_str: str) -> list[int]:
    try:
        return [int(idx.strip()) for idx in gpu_str.split(",")]
    except ValueError:
        raise ValueError("GPU idxs must be comma-separated numbers (e.g., '0,1,2')")


def parse_targets(targets: list[str]) -> tuple[list[int], list[str]]:
    gpu_indices = []
    job_ids = []

    expanded_targets = []
    for target in targets:
        if "," in target:
            expanded_targets.extend(target.split(","))
        else:
            expanded_targets.append(target)

    for target in expanded_targets:
        if target.strip().isdigit():
            gpu_indices.append(int(target.strip()))
        else:
            job_ids.append(target.strip())

    return gpu_indices, job_ids


def expand_job_commands(commands: list[str], repeat: int = 1) -> list[str]:
    expanded_commands = []

    for command in commands:
        # For example, "python train.py --model={gpt2,bert}"
        if "{" in command and "}" in command:
            param_str = re.findall(r"\{([^}]+)\}", command)
            if not param_str:
                expanded_commands.append(command)
                continue
            params = [p.strip().split(",") for p in param_str]
            for combo in itertools.product(*[[v.strip() for v in param] for param in params]):
                temp_cmd = command
                for value in combo:
                    temp_cmd = re.sub(r"\{[^}]+\}", value, temp_cmd, count=1)
                expanded_commands.append(temp_cmd)
        else:
            expanded_commands.append(command)

    return expanded_commands * repeat if repeat > 1 else expanded_commands


def confirm_action(action_description: str, bypass: bool = False) -> bool:
    if bypass:
        return True

    response = input(f"\n{colored('?', 'blue', attrs=['bold'])} {action_description} [y/N] ").lower().strip()
    print()  # newline
    return response == "y"


def ask_yes_no(question: str, default: bool = True) -> bool:
    default_text = "YES" if default else "NO"
    default_prompt = f"[press ENTER for {colored(default_text, 'cyan')}]"
    prompt = f"{colored('?', 'blue', attrs=['bold'])} {question} {default_prompt}: "

    while True:
        answer = input(prompt).strip().lower()
        if not answer:
            print(colored(f"Using default: {default_text}", "cyan"))
            return default
        elif answer in ["y", "yes"]:
            return True
        elif answer in ["n", "no"]:
            return False
        else:
            print(colored("Please answer with 'yes' or 'no'", "yellow"))


def get_user_input(prompt: str, default: str = "", required: bool = False) -> str:
    if default:
        default_display = f" [press ENTER for {colored(default, 'cyan')}]"
    else:
        default_display = ""

    while True:
        result = input(f"{colored('?', 'blue', attrs=['bold'])} {prompt}{default_display}: ").strip()
        if not result:
            if default:
                print(colored(f"Using default: {default}", "cyan"))
                return default
            elif required:
                print(colored("This field is required.", "red"))
                continue
        return result or ""


def open_file_in_editor(file_path: str | pl.Path) -> None:
    # Try to get the editor from environment variables in order of preference
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    # Fall back to common editors if not specified
    if not editor:
        # Check if common editors are available
        for ed in ["nano", "vim", "vi", "notepad", "gedit"]:
            try:
                subprocess.run(["which", ed], capture_output=True, check=False)
                editor = ed
                break
            except (subprocess.SubprocessError, FileNotFoundError):
                continue

    # If still no editor found, default to nano
    if not editor:
        editor = "nano"

    try:
        subprocess.run([editor, str(file_path)], check=True)
        print(colored(f"Opened {file_path} in {editor}", "green"))
    except (subprocess.SubprocessError, FileNotFoundError):
        print(colored(f"Failed to open {file_path} with {editor}", "red"))
        print(f"You can edit the file manually at: {file_path}")
