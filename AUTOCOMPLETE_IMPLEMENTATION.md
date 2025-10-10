# Nexus CLI Autocomplete Implementation Plan

## Goal
Enable seamless shell autocomplete for `nx` commands with automatic setup on first invocation.

## User Experience

### First Time Usage
```bash
$ nx
┌─────────────────────────────────────────────────┐
│ Nexus CLI Autocomplete Setup                    │
├─────────────────────────────────────────────────┤
│ Autocomplete enables tab-completion for:        │
│  • Commands (add, run, kill, etc.)              │
│  • Flags (-r, -p, --priority, etc.)             │
│  • File paths in your commands                  │
│                                                  │
│ Detected shell: bash (~/.bashrc)                │
│                                                  │
│ This will add the following line:               │
│   eval "$(register-python-argcomplete nx)"      │
│                                                  │
│ Install autocomplete? [Y/n]:                    │
└─────────────────────────────────────────────────┘

> y

✓ Autocomplete installed!
→ Reload your shell: source ~/.bashrc
  Or open a new terminal

[continues with normal nx command...]
```

### After Setup
```bash
$ nx add -r 4 python train.py --config experiments/<TAB>
# Autocompletes paths, flags, commands!
```

## Architecture

### Components

#### 1. Shell Detection System
**File:** `src/nexus/cli/shell_completion.py` (NEW)

Responsibilities:
- Detect current shell (bash/zsh/fish)
- Find appropriate RC file location
- Handle custom/non-standard setups
- Validate shell compatibility

#### 2. Completion Installer
**File:** `src/nexus/cli/shell_completion.py`

Responsibilities:
- Check if completion already installed
- Generate shell-specific completion code
- Safely modify RC files
- Create backup before modification
- Set installation flag

#### 3. Invocation Hook
**File:** `src/nexus/cli/main.py`

Responsibilities:
- Run completion check on every nx invocation
- Show prompt only if not installed
- Fast-path if already installed
- Handle user rejection gracefully

### Shell Support Matrix

| Shell | RC File        | Completion Method          | Status |
|-------|----------------|----------------------------|--------|
| bash  | ~/.bashrc      | argcomplete register       | ✅     |
| zsh   | ~/.zshrc       | argcomplete register       | ✅     |
| fish  | ~/.config/fish | argcomplete --fish         | ✅     |
| sh    | ~/.profile     | Fallback to bash method    | ⚠️     |
| other | -              | Manual instructions only   | ⚠️     |

## Implementation Details

### Part 1: Shell Detection

```python
# src/nexus/cli/shell_completion.py

import os
import pathlib as pl
import subprocess
import typing as tp
from dataclasses import dataclass

@dataclass
class ShellInfo:
    name: str  # bash, zsh, fish
    rc_path: pl.Path
    completion_command: str
    detected_method: str  # SHELL env, parent process, etc.

def detect_shell() -> ShellInfo | None:
    """
    Multi-method shell detection with fallbacks.

    Detection order:
    1. $SHELL environment variable
    2. Parent process inspection (ps -p $PPID)
    3. /etc/passwd entry
    4. Default to bash if all fail
    """

    # Method 1: SHELL environment variable
    shell_env = os.environ.get("SHELL", "")
    if shell_env:
        shell_name = os.path.basename(shell_env)
        if shell_name in ["bash", "zsh", "fish"]:
            rc_path = get_rc_path(shell_name)
            if rc_path:
                return ShellInfo(
                    name=shell_name,
                    rc_path=rc_path,
                    completion_command=get_completion_command(shell_name),
                    detected_method="SHELL_ENV"
                )

    # Method 2: Parent process (more reliable for nested shells)
    try:
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=1
        )
        shell_name = result.stdout.strip()
        if shell_name in ["bash", "zsh", "fish"]:
            rc_path = get_rc_path(shell_name)
            if rc_path:
                return ShellInfo(
                    name=shell_name,
                    rc_path=rc_path,
                    completion_command=get_completion_command(shell_name),
                    detected_method="PARENT_PROCESS"
                )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    # Method 3: Check common RC files exist
    for shell_name in ["bash", "zsh", "fish"]:
        rc_path = get_rc_path(shell_name)
        if rc_path and rc_path.exists():
            return ShellInfo(
                name=shell_name,
                rc_path=rc_path,
                completion_command=get_completion_command(shell_name),
                detected_method="RC_FILE_EXISTS"
            )

    return None

def get_rc_path(shell: str) -> pl.Path | None:
    """
    Get RC file path with support for custom locations.

    Checks in order:
    1. Custom environment variable (NEXUS_SHELL_RC)
    2. Shell-specific env vars (BASH_ENV, ZDOTDIR)
    3. Standard locations
    """
    home = pl.Path.home()

    # Custom override
    custom_rc = os.environ.get("NEXUS_SHELL_RC")
    if custom_rc:
        return pl.Path(custom_rc)

    if shell == "bash":
        # Check BASH_ENV first
        bash_env = os.environ.get("BASH_ENV")
        if bash_env:
            return pl.Path(bash_env)

        # Standard locations in order of preference
        candidates = [
            home / ".bashrc",
            home / ".bash_profile",
            home / ".profile"
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        # Default to .bashrc even if doesn't exist
        return home / ".bashrc"

    elif shell == "zsh":
        # Check ZDOTDIR
        zdotdir = os.environ.get("ZDOTDIR", str(home))
        zshrc = pl.Path(zdotdir) / ".zshrc"
        if zshrc.exists():
            return zshrc
        # Fallback
        return home / ".zshrc"

    elif shell == "fish":
        config_home = os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        return pl.Path(config_home) / "fish" / "config.fish"

    return None

def get_completion_command(shell: str) -> str:
    """Generate shell-specific completion command."""
    if shell == "bash":
        return 'eval "$(register-python-argcomplete nx)"'
    elif shell == "zsh":
        # zsh needs compinit loaded first
        return '''
# Load zsh completions
autoload -U compinit && compinit
eval "$(register-python-argcomplete nx)"
'''
    elif shell == "fish":
        return 'register-python-argcomplete --fish nx | source'
    return ""
```

### Part 2: Installation Logic

```python
# src/nexus/cli/shell_completion.py (continued)

def get_flag_path() -> pl.Path:
    """Path to flag indicating completion is installed."""
    return pl.Path.home() / ".nexus" / ".completion_installed"

def is_completion_installed() -> bool:
    """Fast check if we've already installed completion."""
    return get_flag_path().exists()

def is_completion_in_rc(rc_path: pl.Path, shell: str) -> bool:
    """Check if completion command already exists in RC file."""
    if not rc_path.exists():
        return False

    with open(rc_path) as f:
        content = f.read()

    # Check for our marker or the actual command
    markers = [
        "register-python-argcomplete nx",
        "# Nexus CLI autocomplete"
    ]
    return any(marker in content for marker in markers)

def backup_rc_file(rc_path: pl.Path) -> pl.Path:
    """Create backup of RC file before modification."""
    import shutil
    import time

    timestamp = int(time.time())
    backup_path = rc_path.with_suffix(f".nexus-backup-{timestamp}")
    shutil.copy2(rc_path, backup_path)
    return backup_path

def install_completion(shell_info: ShellInfo, skip_prompt: bool = False) -> tuple[bool, str]:
    """
    Install completion to shell RC file.

    Returns:
        (success: bool, message: str)
    """
    rc_path = shell_info.rc_path

    # Already in RC file
    if is_completion_in_rc(rc_path, shell_info.name):
        flag_path = get_flag_path()
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()
        return True, "already_installed"

    # Ensure RC file exists
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    if not rc_path.exists():
        rc_path.touch()

    # Create backup
    try:
        backup_path = backup_rc_file(rc_path)
    except Exception as e:
        return False, f"backup_failed: {e}"

    # Append completion command
    try:
        with open(rc_path, "a") as f:
            f.write("\n")
            f.write("# Nexus CLI autocomplete (added by nx)\n")
            f.write(shell_info.completion_command)
            f.write("\n")
    except Exception as e:
        return False, f"write_failed: {e}"

    # Set flag
    flag_path = get_flag_path()
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()

    return True, f"installed_to:{rc_path}"

def uninstall_completion() -> bool:
    """Remove completion setup (for testing/debugging)."""
    flag_path = get_flag_path()
    if flag_path.exists():
        flag_path.unlink()
    return True
```

### Part 3: User Prompt System

```python
# src/nexus/cli/shell_completion.py (continued)

from termcolor import colored

def show_completion_prompt(shell_info: ShellInfo) -> bool:
    """
    Show interactive prompt for completion installation.

    Returns:
        True if user accepts, False if rejects
    """
    print()
    print(colored("┌" + "─" * 60 + "┐", "blue"))
    print(colored("│ Nexus CLI Autocomplete Setup" + " " * 32 + "│", "blue"))
    print(colored("├" + "─" * 60 + "┤", "blue"))
    print(colored("│ Autocomplete enables tab-completion for:" + " " * 20 + "│", "white"))
    print(colored("│  • Commands (add, run, kill, etc.)" + " " * 27 + "│", "white"))
    print(colored("│  • Flags (-r, -p, --priority, etc.)" + " " * 25 + "│", "white"))
    print(colored("│  • File paths in your commands" + " " * 30 + "│", "white"))
    print(colored("│" + " " * 61 + "│", "white"))
    print(colored(f"│ Detected: {shell_info.name} ({shell_info.rc_path})" +
                  " " * (60 - len(f"Detected: {shell_info.name} ({shell_info.rc_path})")) + "│", "cyan"))
    print(colored("│" + " " * 61 + "│", "white"))
    print(colored("│ This will add the following line:" + " " * 28 + "│", "white"))

    # Show the actual command that will be added
    cmd_preview = shell_info.completion_command.strip().split('\n')[0]
    if len(cmd_preview) > 56:
        cmd_preview = cmd_preview[:53] + "..."
    print(colored(f"│   {cmd_preview}" + " " * (60 - len(cmd_preview) - 3) + "│", "yellow"))
    print(colored("│" + " " * 61 + "│", "white"))
    print(colored("└" + "─" * 60 + "┘", "blue"))
    print()

    response = input(colored("Install autocomplete? [Y/n]: ", "blue", attrs=["bold"]))
    return response.lower() in ["", "y", "yes"]

def show_success_message(shell_info: ShellInfo) -> None:
    """Show success message with reload instructions."""
    print()
    print(colored("✓ Autocomplete installed!", "green", attrs=["bold"]))
    print(colored(f"→ Reload your shell: ", "cyan") +
          colored(f"source {shell_info.rc_path}", "yellow", attrs=["bold"]))
    print(colored("  Or open a new terminal", "cyan"))
    print()

def show_skip_message() -> None:
    """Show message when user skips installation."""
    print()
    print(colored("Skipped autocomplete installation.", "yellow"))
    print(colored("To install later, run: nx setup", "cyan"))
    print()

def show_manual_instructions() -> None:
    """Show manual installation instructions when auto-detect fails."""
    print()
    print(colored("Could not auto-detect shell configuration.", "yellow"))
    print()
    print(colored("To enable autocomplete manually:", "cyan"))
    print()
    print(colored("For bash:", "white", attrs=["bold"]))
    print(colored('  echo \'eval "$(register-python-argcomplete nx)"\' >> ~/.bashrc', "yellow"))
    print(colored("  source ~/.bashrc", "yellow"))
    print()
    print(colored("For zsh:", "white", attrs=["bold"]))
    print(colored('  echo \'eval "$(register-python-argcomplete nx)"\' >> ~/.zshrc', "yellow"))
    print(colored("  source ~/.zshrc", "yellow"))
    print()
    print(colored("For fish:", "white", attrs=["bold"]))
    print(colored("  register-python-argcomplete --fish nx > ~/.config/fish/completions/nx.fish", "yellow"))
    print()
```

### Part 4: Main Integration

```python
# src/nexus/cli/main.py

import argcomplete
from nexus.cli import shell_completion

def setup_argcomplete_and_check(parser: argparse.ArgumentParser) -> None:
    """
    Setup argcomplete and check if shell completion needs installation.

    This runs on EVERY nx invocation but is optimized for speed:
    - Fast path: if flag exists, skip entirely (~1ms)
    - Slow path: only on first run, show prompt
    """
    # Enable argcomplete parsing
    argcomplete.autocomplete(parser)

    # Fast path: already installed
    if shell_completion.is_completion_installed():
        return

    # Detect shell
    shell_info = shell_completion.detect_shell()

    # Could not detect shell
    if not shell_info:
        # Set flag to not show this again
        flag_path = shell_completion.get_flag_path()
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()
        shell_completion.show_manual_instructions()
        return

    # Check if already in RC (maybe user added manually)
    if shell_completion.is_completion_in_rc(shell_info.rc_path, shell_info.name):
        flag_path = shell_completion.get_flag_path()
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()
        return

    # Show prompt
    if shell_completion.show_completion_prompt(shell_info):
        success, message = shell_completion.install_completion(shell_info)
        if success:
            shell_completion.show_success_message(shell_info)
        else:
            print(colored(f"Installation failed: {message}", "red"))
            shell_completion.show_manual_instructions()
    else:
        # User said no - set flag to not ask again
        flag_path = shell_completion.get_flag_path()
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()
        shell_completion.show_skip_message()

def main() -> None:
    parser = create_parser()

    # Setup argcomplete and check for shell completion
    setup_argcomplete_and_check(parser)

    args = parser.parse_args()

    # ... rest of main continues
```

### Part 5: nx add REMAINDER Pattern

```python
# src/nexus/cli/main.py - add_job_management_parsers()

def add_job_management_parsers(subparsers) -> None:
    # Add jobs to queue
    add_parser = subparsers.add_parser("add", help="Add job(s) to queue")

    # ALL FLAGS FIRST (before commands)
    add_parser.add_argument("-r", "--repeat", type=int, default=1,
                           help="Repeat the command multiple times")
    add_parser.add_argument("-p", "--priority", type=int, default=0,
                           help="Set job priority (higher values run first)")
    add_parser.add_argument("-n", "--notify", nargs="+",
                           help="Additional notification types for this job")
    add_parser.add_argument("-s", "--silent", action="store_true",
                           help="Disable all notifications for this job")
    add_parser.add_argument("-i", "--gpu-idxs", dest="gpu_idxs",
                           help="Specific GPU indices to run on (e.g., '0' or '0,1')")
    add_parser.add_argument("-g", "--gpus", type=int, default=1,
                           help="Number of GPUs to use for the job")
    add_parser.add_argument("-f", "--force", action="store_true",
                           help="Ignore GPU blacklist")
    add_parser.add_argument("-y", "--yes", action="store_true",
                           help="Skip confirmation prompt")

    # COMMANDS LAST - consumes all remaining args
    add_parser.add_argument("commands", nargs=argparse.REMAINDER,
                           help="Command to add (everything after flags, no quotes needed)")

    # ... rest of parsers
```

```python
# src/nexus/cli/jobs.py - add_jobs()

def add_jobs(
    cfg: config.NexusCliConfig,
    commands: list[str],
    repeat: int,
    priority: int = 0,
    gpu_idxs_str: str | None = None,
    num_gpus: int = 1,
    notification_types: list[NotificationType] | None = None,
    integration_types: list[IntegrationType] | None = None,
    force: bool = False,
    bypass_confirm: bool = False,
    silent: bool = False,
) -> None:
    try:
        # Handle REMAINDER args
        if not commands:
            print(colored("Error: No command provided", "red"))
            print(colored("Usage: nx add [flags] command", "yellow"))
            print(colored("Example: nx add -r 4 python train.py --lr 0.001", "yellow"))
            return

        # Join all remaining args into single command string
        command_str = " ".join(commands)
        commands = [command_str]

        # Rest of function continues unchanged...
        gpu_idxs = None
        if gpu_idxs_str:
            gpu_idxs = utils.parse_gpu_list(gpu_idxs_str)

        expanded_commands = utils.expand_job_commands(commands, repeat=repeat)
        # ... rest continues
```

## Edge Cases & Handling

### 1. User in virtualenv but nx installed globally
**Problem:** `register-python-argcomplete nx` might not find nx

**Solution:**
```python
def verify_argcomplete_works() -> bool:
    """Verify that argcomplete can find nx."""
    try:
        result = subprocess.run(
            ["register-python-argcomplete", "nx"],
            capture_output=True,
            timeout=2
        )
        return result.returncode == 0
    except:
        return False

# In install_completion(), before writing:
if not verify_argcomplete_works():
    return False, "argcomplete_not_found"
```

### 2. Multiple shells configured
**Problem:** User has both bash and zsh

**Solution:** Install in the detected shell only. If they switch shells, nx will detect and offer to install again.

### 3. RC file symlinked elsewhere
**Problem:** ~/.bashrc -> ~/dotfiles/bashrc

**Solution:** `pl.Path.resolve()` before modification to follow symlinks.

### 4. User denies permission
**Problem:** Can't write to RC file

**Solution:** Catch permission errors, show manual instructions.

### 5. Completion already installed but flag missing
**Problem:** User manually added completion, flag doesn't exist

**Solution:** `is_completion_in_rc()` check before showing prompt.

### 6. nx run in script/non-interactive
**Problem:** stdin not a tty, can't prompt

**Solution:**
```python
def is_interactive_shell() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()

# Only show prompt if interactive
if is_interactive_shell() and shell_completion.show_completion_prompt(...):
    ...
```

## Testing Strategy

### Unit Tests
```python
# tests/cli/test_shell_completion.py

def test_detect_shell_from_env():
    with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
        shell_info = detect_shell()
        assert shell_info.name == "bash"

def test_detect_shell_from_parent_process():
    # Mock ps output
    ...

def test_get_rc_path_custom_override():
    with patch.dict(os.environ, {"NEXUS_SHELL_RC": "/custom/path"}):
        path = get_rc_path("bash")
        assert path == pl.Path("/custom/path")

def test_completion_command_bash():
    cmd = get_completion_command("bash")
    assert "register-python-argcomplete" in cmd

def test_completion_command_zsh():
    cmd = get_completion_command("zsh")
    assert "compinit" in cmd
    assert "register-python-argcomplete" in cmd

def test_is_completion_in_rc():
    # Create temp RC with completion
    # Verify detection
    ...

def test_backup_rc_file():
    # Create temp RC
    # Backup
    # Verify backup exists and content matches
    ...
```

### Integration Tests
```bash
# Manual test script

# Test 1: Fresh install
rm -f ~/.nexus/.completion_installed
nx  # Should show prompt

# Test 2: Already installed
nx  # Should skip prompt (fast)

# Test 3: Manual RC entry
echo 'eval "$(register-python-argcomplete nx)"' >> ~/.bashrc
rm -f ~/.nexus/.completion_installed
nx  # Should detect existing, skip prompt

# Test 4: Different shell
export SHELL=/bin/zsh
rm -f ~/.nexus/.completion_installed
nx  # Should detect zsh

# Test 5: Non-interactive
echo "nx" | bash  # Should not prompt, set flag anyway
```

## Performance Considerations

### First Run (Uncached)
- Shell detection: ~2ms
- RC file check: ~1ms
- User prompt: (blocking)
- Installation: ~5ms
- **Total:** ~8ms + user interaction time

### Subsequent Runs (Cached)
- Flag check: <1ms
- **Total:** <1ms (negligible)

### Optimization
```python
# Fast path is VERY fast
def is_completion_installed() -> bool:
    # Simple file existence check, no I/O beyond stat
    return get_flag_path().exists()  # ~0.1ms
```

## Uninstallation

```bash
# For users who want to remove
nx config uninstall-completion

# Or manual
rm ~/.nexus/.completion_installed
# Remove line from RC file manually
```

## Documentation

### README.md section
````markdown
## Autocomplete

Nexus CLI supports tab-completion for all commands and file paths.

On first run, nx will detect your shell and offer to install autocomplete:
```bash
$ nx
Install autocomplete? [Y/n]: y
✓ Autocomplete installed!
→ Reload your shell: source ~/.bashrc
```

After reloading your shell, autocomplete works everywhere:
```bash
$ nx add -r 4 python train.py --config <TAB>
                                         ↑ autocompletes paths!
```

### Manual Installation

If auto-detection fails, install manually:

**Bash:**
```bash
echo 'eval "$(register-python-argcomplete nx)"' >> ~/.bashrc
source ~/.bashrc
```

**Zsh:**
```bash
echo 'eval "$(register-python-argcomplete nx)"' >> ~/.zshrc
source ~/.zshrc
```

**Fish:**
```bash
register-python-argcomplete --fish nx > ~/.config/fish/completions/nx.fish
```
````

## Implementation Checklist

- [ ] Add argcomplete dependency to pyproject.toml
- [ ] Create src/nexus/cli/shell_completion.py with all functions
- [ ] Update src/nexus/cli/main.py with argcomplete setup
- [ ] Change nx add to use argparse.REMAINDER
- [ ] Update src/nexus/cli/jobs.py add_jobs() to join REMAINDER
- [ ] Add unit tests for shell_completion.py
- [ ] Add integration tests for full flow
- [ ] Update README.md with autocomplete section
- [ ] Test on bash, zsh, fish
- [ ] Test edge cases (symlinks, permissions, etc.)
- [ ] Add uninstall command

## Line Count Estimate

| File | Lines | Purpose |
|------|-------|---------|
| shell_completion.py | ~250 | Detection, installation, prompts |
| main.py (changes) | ~15 | Integration hook |
| jobs.py (changes) | ~8 | REMAINDER handling |
| pyproject.toml | ~1 | Dependency |
| tests | ~150 | Unit tests |
| **Total** | **~424** | Complete implementation |

## Future Enhancements

1. **Smart suggestions** - Suggest recent commands/job IDs
2. **Flag value completion** - Complete priority values, GPU indices
3. **Command history** - Complete from previous nx add commands
4. **Dynamic completion** - Query server for job IDs, GPU states
5. **Cross-shell config** - Install in all detected shells at once
