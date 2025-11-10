import dataclasses as dc
import hashlib
import itertools
import os
import pathlib as pl
import random
import re
import subprocess
import tempfile
import time
import typing as tp

import base58
from termcolor import colored

# Types
Color = tp.Literal["grey", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
Attribute = tp.Literal["bold", "dark", "underline", "blink", "reverse", "concealed"]


@dc.dataclass(frozen=True)
class GitArtifactContext:
    job_id: str
    artifact_id: str | None
    git_repo_url: str | None
    branch_name: str
    commit_sha: str | None
    temp_branch: str | None
    original_branch: str | None
    we_created_stash: bool
    git_tag: str | None


# CLI Output Helpers
def print_header(title: str) -> None:
    print(colored(title, "blue", attrs=["bold"]))


def print_item(key: str, value: str | int, color: Color = "cyan") -> None:
    print(f"{colored(key, color)}: {value}")


def print_bullet(text: str, color: Color = "blue") -> None:
    print(f"  {colored('•', color)} {text}")


def print_error(message: str) -> None:
    print(colored(f"Error: {message}", "red"))


def print_warning(message: str) -> None:
    print(colored(message, "yellow"))


def print_success(message: str) -> None:
    print(colored(message, "green"))


def print_health_warning() -> None:
    print(colored("\n⚠️  WARNING: System health is UNHEALTHY! Jobs may fail or perform poorly.", "red", attrs=["bold"]))
    print(colored("     Run 'nx health' for details. Consider addressing issues before submitting jobs.", "red"))


def print_hint(command: str, description: str) -> None:
    print(f"\nTo {description}: {colored(command, 'green')}")


def format_key_value(key: str, value: str | int, key_color: Color = "cyan") -> str:
    return f"{colored(key, key_color)}: {value}"


def is_sensitive_key(key: str) -> bool:
    sensitive_keywords = ["key", "token", "secret", "password", "sid", "number"]
    return any(keyword in key.lower() for keyword in sensitive_keywords)


def generate_job_id() -> str:
    timestamp = str(time.time()).encode()
    random_bytes = os.urandom(4)
    hash_input = timestamp + random_bytes
    hash_bytes = hashlib.sha256(hash_input).digest()[:4]
    return base58.b58encode(hash_bytes).decode()[:6].lower()


def is_working_tree_dirty() -> bool:
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True)
    return bool(result.stdout.strip())


def save_working_state() -> tuple[str, str, str, bool]:
    try:
        original_branch = get_current_git_branch()

        is_dirty = is_working_tree_dirty()
        if is_dirty:
            subprocess.run(["git", "stash", "-u"], check=True, capture_output=True)

        temp_branch = f"nexus-tmp-{int(time.time())}-{generate_job_id()}"
        subprocess.run(["git", "checkout", "-b", temp_branch], check=True, capture_output=True)

        if is_dirty:
            subprocess.run(["git", "stash", "apply"], check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Nexus temporary commit"], check=True, capture_output=True)

        commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()

        return (original_branch, temp_branch, commit_sha, is_dirty)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to save working state: {e}")


def restore_working_state(original_branch: str, temp_branch: str, we_created_stash: bool) -> None:
    subprocess.run(["git", "checkout", original_branch], check=True, capture_output=True)
    if we_created_stash:
        subprocess.run(["git", "stash", "pop"], check=True, capture_output=True)
    subprocess.run(["git", "branch", "-D", temp_branch], check=True, capture_output=True)


def prepare_git_artifact(enable_git_tag_push: bool, target_name: str | None = None) -> GitArtifactContext:
    from nexus.cli import api_client

    job_id = generate_job_id()
    branch_name = get_current_git_branch()

    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        raise

    temp_branch = None
    original_branch = None
    commit_sha = None
    we_created_stash = False

    try:
        if is_working_tree_dirty():
            original_branch, temp_branch, commit_sha, we_created_stash = save_working_state()
        else:
            result = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True)
            commit_sha = result.stdout.strip()

        result = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True)
        git_repo_url = result.stdout.strip() or "unknown-url"

        artifact_id = None
        git_tag = None
        if commit_sha:
            print(colored(f"Checking for existing artifact (tree: {commit_sha[:8]})...", "blue"))
            exists, existing_artifact_id = api_client.check_artifact_by_sha(commit_sha, target_name=target_name)
            if exists and existing_artifact_id:
                print(colored(f"Reusing existing artifact {existing_artifact_id}", "green"))
                artifact_id = existing_artifact_id

        if artifact_id is None:
            print(colored("Creating git archive...", "blue"))
            artifact_data = create_git_archive(temp_branch or "HEAD")

            if enable_git_tag_push and can_push_to_remote("origin"):
                tag_name = f"nexus-{job_id}"
                if commit_sha:
                    ensure_git_tag(tag_name, message=f"Nexus job {job_id}", commit_ref=commit_sha)
                else:
                    ensure_git_tag(tag_name, message=f"Nexus job {job_id}")

                try:
                    push_git_tag(tag_name, remote="origin")
                    print(colored(f"Pushed git tag: {tag_name}", "green"))
                    git_tag = tag_name
                except RuntimeError as e:
                    print()
                    print(colored(f"ERROR: Failed to push git tag {tag_name}", "red", attrs=["bold"]))
                    print(colored(f"       {e}", "red"))
                    print(colored("       Common fixes:", "red"))
                    print(colored("       • Check git authentication (SSH keys or credentials)", "red"))
                    print(colored("       • Verify network connectivity to remote", "red"))
                    print(colored("       • Ensure you have push permissions to the repository", "red"))
                    print(colored("       Job will continue without git tag.", "red"))
                    print()

            print(colored("Uploading git archive...", "blue"))
            artifact_id = api_client.upload_artifact(artifact_data, git_sha=commit_sha, target_name=target_name)
            print(colored(f"Artifact uploaded with ID: {artifact_id}", "green"))

        if temp_branch and original_branch:
            restore_working_state(original_branch, temp_branch, we_created_stash)
            temp_branch_saved = None
            original_branch_saved = None
        else:
            temp_branch_saved = temp_branch
            original_branch_saved = original_branch

        return GitArtifactContext(
            job_id=job_id,
            artifact_id=artifact_id,
            git_repo_url=git_repo_url,
            branch_name=branch_name,
            commit_sha=commit_sha,
            temp_branch=temp_branch_saved,
            original_branch=original_branch_saved,
            we_created_stash=we_created_stash,
            git_tag=git_tag,
        )
    finally:
        if temp_branch and original_branch:
            current = get_current_git_branch()
            if current == temp_branch:
                restore_working_state(original_branch, temp_branch, we_created_stash)


def cleanup_git_state(ctx: GitArtifactContext) -> None:
    if ctx.temp_branch and ctx.original_branch:
        restore_working_state(ctx.original_branch, ctx.temp_branch, ctx.we_created_stash)


def can_push_to_remote(remote: str = "origin") -> bool:
    try:
        result = subprocess.run(["git", "remote", "get-url", remote], capture_output=True, check=True)
        if not result.stdout.strip():
            return False

        subprocess.run(["git", "push", "--dry-run", remote, "HEAD"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


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


def ensure_clean_repo() -> None:
    out = subprocess.check_output(["git", "status", "--porcelain"]).decode().strip()
    if out:
        raise RuntimeError("Refusing to submit: working tree has uncommitted changes.")


def create_git_archive(ref: str = "HEAD") -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as archive:
        subprocess.run(["git", "archive", "--format=tar", ref], stdout=archive, check=True)
    with open(archive.name, "rb") as f:
        data = f.read()
    os.unlink(archive.name)
    return data


def ensure_git_tag(tag_name: str, message: str | None = None, commit_ref: str = "HEAD") -> None:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            return

        args = ["git", "tag", "-a", tag_name]
        if message:
            args += ["-m", message]
        args.append(commit_ref)
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create git tag {tag_name}: {e}")


def push_git_tag(tag_name: str, remote: str = "origin") -> None:
    try:
        subprocess.run(
            ["git", "push", remote, tag_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        error_details = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"Failed to push git tag {tag_name} to {remote}: {error_details}")


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


def _expand_zip_mode(command: str) -> list[str]:
    zip_pattern = r"\{\{([^}]+)\}\}"
    zip_matches = re.findall(zip_pattern, command)

    if not zip_matches:
        return [command]

    param_lists = [[v.strip() for v in match.split(",")] for match in zip_matches]

    max_len = max(len(lst) for lst in param_lists)
    padded_lists = []
    for lst in param_lists:
        if len(lst) < max_len:
            padded = lst + [lst[-1]] * (max_len - len(lst))
            padded_lists.append(padded)
        else:
            padded_lists.append(lst)

    expanded = []
    for values in zip(*padded_lists):
        temp_cmd = command
        for value in values:
            temp_cmd = re.sub(zip_pattern, value, temp_cmd, count=1)
        expanded.append(temp_cmd)

    return expanded


def expand_job_commands(commands: list[str], repeat: int = 1) -> list[str]:
    expanded_commands = []

    for command in commands:
        if "{{" in command and "}}" in command:
            expanded_commands.extend(_expand_zip_mode(command))
        elif "{" in command and "}" in command:
            randint_matches = re.findall(r"\{RANDINT(?::(\d+)(?:,(\d+))?)?\}", command)
            if randint_matches:
                temp_cmd = command
                for min_str, max_str in randint_matches:
                    min_val = int(min_str) if min_str else 0
                    max_val = int(max_str) if max_str else 100
                    rand_val = str(random.randint(min_val, max_val))
                    temp_cmd = re.sub(r"\{RANDINT(?::\d+(?:,\d+)?)?\}", rand_val, temp_cmd, count=1)
                expanded_commands.append(temp_cmd)
            elif re.search(r"\{[^}]+\}", command):
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
        else:
            expanded_commands.append(command)

    return expanded_commands * repeat if repeat > 1 else expanded_commands


def confirm_action(action_description: str, bypass: bool = False) -> bool:
    if bypass:
        return True

    options = f"[{colored('y', 'green')}/{colored('N', 'red')}]"
    response = (
        input(
            f"\n{colored('?', 'blue', attrs=['bold'])} {action_description} {options} [press ENTER for {colored('NO', 'red')}]: "
        )
        .lower()
        .strip()
    )
    print()  # newline
    return response == "y"


def ask_yes_no(question: str, default: bool = True) -> bool:
    default_text = "YES" if default else "NO"
    options = f"[{colored('y', 'green')}/{colored('n', 'red')}]"
    default_prompt = (
        f"[press ENTER for {colored(default_text, 'cyan')}, type {colored('n', 'red')} for no]"
        if default
        else f"[press ENTER for {colored(default_text, 'cyan')}, type {colored('y', 'green')} for yes]"
    )
    prompt = f"{colored('?', 'blue', attrs=['bold'])} {question} {options} {default_prompt}: "

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


def get_user_input(prompt: str, default: str = "", required: bool = False, mask_input: bool = False) -> str:
    if default:
        default_display = f" [press ENTER for {colored(default, 'cyan')}]"
    else:
        default_display = required and " [required]" or ""

    while True:
        if mask_input:
            import getpass

            result = getpass.getpass(f"{colored('?', 'blue', attrs=['bold'])} {prompt}{default_display}: ").strip()
        else:
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
