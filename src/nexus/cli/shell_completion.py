import os
import pathlib as pl
import sys
from dataclasses import dataclass

from termcolor import colored


@dataclass(frozen=True)
class ShellInfo:
    name: str
    rc_path: pl.Path
    completion_command: str


def get_flag_path() -> pl.Path:
    return pl.Path.home() / ".nexus" / ".completion_installed"


def is_completion_installed() -> bool:
    return get_flag_path().exists()


def set_completion_flag() -> None:
    flag_path = get_flag_path()
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()


def detect_shell() -> ShellInfo | None:
    shell_env = os.environ.get("SHELL", "")
    if not shell_env:
        return None

    shell_name = pl.Path(shell_env).name

    if shell_name not in ["bash", "zsh"]:
        return None

    rc_path = _get_rc_path(shell_name)
    if not rc_path:
        return None

    completion_cmd = _get_completion_command(shell_name)

    return ShellInfo(name=shell_name, rc_path=rc_path, completion_command=completion_cmd)


def _get_rc_path(shell: str) -> pl.Path | None:
    home = pl.Path.home()

    if shell == "bash":
        return home / ".bashrc"
    elif shell == "zsh":
        return home / ".zshrc"

    return None


def _get_completion_command(shell: str) -> str:
    if shell == "bash":
        return 'eval "$(register-python-argcomplete nx)"'
    elif shell == "zsh":
        return 'autoload -U compinit && compinit\neval "$(register-python-argcomplete nx)"'
    return ""


def is_completion_in_rc(shell_info: ShellInfo) -> bool:
    if not shell_info.rc_path.exists():
        return False

    try:
        content = shell_info.rc_path.read_text()
        return "register-python-argcomplete nx" in content
    except Exception:
        return False


def install_completion(shell_info: ShellInfo) -> tuple[bool, str]:
    if is_completion_in_rc(shell_info):
        set_completion_flag()
        return True, "already_installed"

    shell_info.rc_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(shell_info.rc_path, "a") as f:
            f.write("\n")
            f.write("# Nexus CLI autocomplete\n")
            f.write(shell_info.completion_command)
            f.write("\n")
    except Exception as e:
        return False, f"write_failed: {e}"

    set_completion_flag()
    return True, "installed"


def show_completion_prompt(shell_info: ShellInfo) -> bool | None:
    print()
    print(colored("Nexus CLI Autocomplete Setup", "blue", attrs=["bold"]))
    print()
    print(colored("Enables natural command syntax for nx add and nx run:", "white"))
    print(colored("  • nx add -r 4 python train.py --lr 0.001", "white"))
    print(colored("  • nx run -g 2 python eval.py --checkpoint best.pt", "white"))
    print(colored("  • Plus tab-completion for flags and file paths", "white"))
    print()
    print(colored(f"Detected: {shell_info.name} ({shell_info.rc_path})", "cyan"))
    print()
    print(colored("This will add:", "white"))
    print(colored(f"  {shell_info.completion_command.split(chr(10))[0]}", "yellow"))
    print()

    if not sys.stdin.isatty():
        set_completion_flag()
        return False

    try:
        response = input(colored("Install autocomplete? [Y/n]: ", "blue", attrs=["bold"]))
        response_lower = response.lower()
        if response_lower in ["", "y", "yes"]:
            return True
        elif response_lower in ["n", "no"]:
            return False
        else:
            return None
    except (EOFError, KeyboardInterrupt):
        return None


def show_success_message(shell_info: ShellInfo) -> None:
    print()
    print(colored("✓ Autocomplete installed!", "green", attrs=["bold"]))
    print(colored("→ Reload your shell: ", "cyan") + colored(f"source {shell_info.rc_path}", "yellow", attrs=["bold"]))
    print(colored("  Or open a new terminal", "cyan"))
    print()


def show_skip_message() -> None:
    print()
    print(colored("Skipped autocomplete installation.", "yellow"))
    print()


def show_manual_instructions() -> None:
    print()
    print(colored("Could not auto-detect shell.", "yellow"))
    print()
    print(colored("To enable autocomplete manually:", "cyan"))
    print()
    print(colored("For bash:", "white", attrs=["bold"]))
    print(colored("  echo 'eval \"$(register-python-argcomplete nx)\"' >> ~/.bashrc", "yellow"))
    print(colored("  source ~/.bashrc", "yellow"))
    print()
    print(colored("For zsh:", "white", attrs=["bold"]))
    print(colored("  echo 'eval \"$(register-python-argcomplete nx)\"' >> ~/.zshrc", "yellow"))
    print(colored("  source ~/.zshrc", "yellow"))
    print()


def check_and_prompt_completion() -> None:
    if is_completion_installed():
        return

    shell_info = detect_shell()

    if not shell_info:
        set_completion_flag()
        show_manual_instructions()
        return

    if is_completion_in_rc(shell_info):
        set_completion_flag()
        return

    result = show_completion_prompt(shell_info)

    if result is True:
        success, message = install_completion(shell_info)
        if success:
            show_success_message(shell_info)
        else:
            print(colored(f"Installation failed: {message}", "red"))
            show_manual_instructions()
    elif result is False:
        set_completion_flag()
        show_skip_message()
    elif result is None:
        print()
        print(colored("Prompt interrupted. You'll be asked again next time.", "yellow"))
        print()
