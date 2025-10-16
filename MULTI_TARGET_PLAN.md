# Multi-Target Support - Bulletproof Implementation Plan (REVISED v2)

## Executive Summary

**Total LOC Impact:** ~260 lines across 5 files
**Estimated Time:** 4-5 hours
**Complexity:** Medium (mostly mechanical, simplified migration)
**Philosophy:** Minimal complexity, maximum integration with existing abstractions

---

## Critical Design Decisions

### 1. **File Naming: By Server Identity (host:port), NOT Target Name**

**Rationale:** Files must be tied to the actual server, not the arbitrary user-provided name.

**Benefits:**
- ✅ Same server can have multiple target names (aliases)
- ✅ Renaming a target doesn't break file references
- ✅ Prevents duplicate SSH keys for same server
- ✅ Migration is simpler (derive from host:port in old config)

**File naming pattern:**
```
~/.ssh/nexus_{host}_{port}_ed25519
~/.nexus/{host}_{port}_cert.pem
```

**Examples:**
```
~/.ssh/nexus_gpu-cluster-1.example.com_54323_ed25519
~/.nexus/gpu-cluster-1.example.com_54323_cert.pem
```

**Edge case handling:**
```python
def _sanitize_host_for_filename(host: str) -> str:
    return host.replace(":", "_").replace("/", "_").replace("\\", "_")
```

### 2. **Auto-Fetch Server Name from API**

**User provides:** host, port, token
**System fetches:** server's `node_name` from `/v1/server/status`
**Target name becomes:** `node_name` (not user-provided)

**Rationale:**
- ✅ Eliminates user naming errors
- ✅ Consistent naming across all clients
- ✅ Validates connection during setup
- ✅ Prevents accidental duplicates

**Flow:**
```
User: nx targets add
  → Prompt: Host? gpu-cluster-1.example.com
  → Prompt: Port? [54323]
  → Prompt: API Token? ***********
  → Download cert (validates HTTPS)
  → Connect to /v1/server/status
  → Extract node_name: "gpu-cluster-1"
  → Create target named "gpu-cluster-1"
  → Generate SSH key
  → Register SSH key
  → Done
```

### 3. **Migration Strategy: Config-Only**

**Reality Check:**
- Master branch (100% of production users): No `host` field, no remote support at all
- Feature branch (dev only - you): Has `host` field but this branch will be squashed/rebased

**Approach:**
- Master users: No migration needed (Pydantic defaults handle it)
- Feature branch users: Config migration only (no files to rename - they don't exist!)

**Key Insight:**
- Master has zero remote support → no SSH keys or certs exist
- Only migrate config structure, file renaming is unnecessary
- Simpler migration = less code, fewer edge cases

---

## Detailed Implementation

### 1. **config.py** (87 → ~155 lines, +68 LOC)

#### A. Add TargetConfig (after line 12, ~8 lines)
```python
class TargetConfig(pyd.BaseModel):
    host: str
    port: int = pyd.Field(default=54323)
    protocol: str = pyd.Field(default="https")
    api_token: str | None = None
```

#### B. Modify NexusCliConfig (replace lines 22-30, net +3 lines)
```python
class NexusCliConfig(pyds.BaseSettings):
    targets: dict[str, TargetConfig] = pyd.Field(default_factory=dict)
    default_target: str | None = pyd.Field(default=None)
    user: str | None = pyd.Field(default=None)
    default_integrations: list[IntegrationType] = []
    default_notifications: list[NotificationType] = []
    enable_git_tag_push: bool = pyd.Field(default=True)

    model_config = {"env_prefix": "NEXUS_", "env_nested_delimiter": "__", "extra": "ignore"}
```

#### C. Add get_active_target() (after line 36, ~18 lines)
```python
def get_active_target(target_name: str | None) -> tuple[str, TargetConfig | None]:
    cfg = load_config()

    if target_name:
        if target_name == "local":
            return "local", None
        if target_name not in cfg.targets:
            raise ValueError(f"Target '{target_name}' not found. Use 'nx targets list' to see available targets.")
        return target_name, cfg.targets[target_name]

    if cfg.default_target:
        if cfg.default_target == "local":
            return "local", None
        if cfg.default_target not in cfg.targets:
            raise ValueError(f"Default target '{cfg.default_target}' not found in config")
        return cfg.default_target, cfg.targets[cfg.default_target]

    return "local", None
```

#### D. Update path helpers (replace lines 39-44, ~12 lines)

**Key change:** Take `host` and `port` instead of `target_name`

```python
def get_ssh_key_path(host: str, port: int) -> pl.Path:
    safe_host = host.replace(":", "_").replace("/", "_").replace("\\", "_")
    return pl.Path.home() / ".ssh" / f"nexus_{safe_host}_{port}_ed25519"


def get_server_cert_path(host: str, port: int) -> pl.Path:
    safe_host = host.replace(":", "_").replace("/", "_").replace("\\", "_")
    return pl.Path.home() / ".nexus" / f"{safe_host}_{port}_cert.pem"
```

#### E. Add config-only migration (~25 lines)

**Replace lines 60-72:**

```python
def load_config() -> NexusCliConfig:
    create_default_config()
    config_path = get_config_path()

    if config_path.exists():
        try:
            with open(config_path) as f:
                config_dict = toml.load(f)

            # Only migrate feature branch remote configs (config structure only)
            if "host" in config_dict and config_dict.get("host") not in [None, "localhost", "127.0.0.1"]:
                config_dict = _migrate_remote_config(config_dict)

            return NexusCliConfig(**config_dict)
        except Exception as e:
            print(f"Error loading config: {e}")
            return NexusCliConfig()
    return NexusCliConfig()


def _migrate_remote_config(old_dict: dict) -> dict:
    from termcolor import colored

    host = old_dict["host"]
    port = old_dict.get("port", 54323)

    print(colored(f"Migrating config to target '{host}'", "yellow"))

    return {
        "targets": {
            host: {
                "host": host,
                "port": port,
                "protocol": old_dict.get("protocol", "https"),
                "api_token": old_dict.get("api_token"),
            }
        },
        "default_target": host,
        "user": old_dict.get("user"),
        "default_integrations": old_dict.get("default_integrations", []),
        "default_notifications": old_dict.get("default_notifications", []),
        "enable_git_tag_push": old_dict.get("enable_git_tag_push", True),
    }
```

---

### 2. **api_client.py** (248 → ~268 lines, +20 LOC)

#### A. Rewrite 3 helper functions (~30 lines total)

**_get_verify()** (lines 12-21):
```python
def _get_verify(target_name: str | None = None) -> str | bool:
    active_name, target_cfg = config.get_active_target(target_name)

    if target_cfg is None:
        return True

    if target_cfg.protocol == "http":
        return True

    cert = config.get_server_cert_path(target_cfg.host, target_cfg.port)
    if not cert.exists():
        raise FileNotFoundError(
            f"SSL certificate not found at {cert}. Run 'nx targets add' to reconfigure."
        )
    return str(cert)
```

**_get_headers()** (lines 72-77):
```python
def _get_headers(target_name: str | None = None) -> dict[str, str]:
    _, target_cfg = config.get_active_target(target_name)
    if target_cfg and target_cfg.api_token:
        return {"Authorization": f"Bearer {target_cfg.api_token}"}
    return {}
```

**get_api_base_url()** (lines 66-69):
```python
def get_api_base_url(target_name: str | None = None) -> str:
    _, target_cfg = config.get_active_target(target_name)

    if target_cfg is None:
        return "http://localhost:54323/v1"

    return f"{target_cfg.protocol}://{target_cfg.host}:{target_cfg.port}/v1"
```

#### B. Add target_name parameter to 15 API functions (~15 lines)

**Pattern (same for all 15 functions):**
```python
# Before:
def get_gpus() -> list[dict]:
    response = requests.get(f"{get_api_base_url()}/gpus", ...)

# After:
def get_gpus(target_name: str | None = None) -> list[dict]:
    response = requests.get(
        f"{get_api_base_url(target_name)}/gpus",
        headers=_get_headers(target_name),
        verify=_get_verify(target_name)
    )
```

**Functions to modify:**
1. `check_api_connection` (line 80)
2. `get_gpus` (line 92)
3. `get_jobs` (line 99)
4. `get_job` (line 107)
5. `get_job_logs` (line 114)
6. `get_server_status` (line 124)
7. `get_detailed_health` (line 131)
8. `check_heartbeat` (line 140)
9. `upload_artifact` (line 149)
10. `add_job` (line 158)
11. `kill_running_jobs` (line 165)
12. `remove_queued_jobs` (line 182)
13. `edit_job` (line 199)
14. `manage_blacklist` (line 222)
15. `register_ssh_key` (line 244)

---

### 3. **setup.py** (361 → ~449 lines, +88 LOC)

#### A. Update helper functions (lines 281-321)

**_download_server_certificate()** (line 281):
```python
def _download_server_certificate(host: str, port: int) -> pl.Path:
    import ssl

    print(colored("\nDownloading server certificate...", "cyan"))
    cert_pem = ssl.get_server_certificate((host, port), timeout=10)
    cert_path = config.get_server_cert_path(host, port)
    cert_path.parent.mkdir(exist_ok=True)
    cert_path.write_text(cert_pem)
    print(colored("Certificate saved", "green"))
    return cert_path
```

**_generate_ssh_key()** (line 292):
```python
def _generate_ssh_key(host: str, port: int) -> pl.Path:
    import subprocess

    ssh_key_path = config.get_ssh_key_path(host, port)
    if not ssh_key_path.exists():
        print(colored(f"\nGenerating SSH key at {ssh_key_path}...", "cyan"))
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(ssh_key_path), "-N", "", "-C", "nexus-client"],
            check=True,
            capture_output=True,
        )
    return ssh_key_path
```

**_register_ssh_key_with_server()** (line 306):
```python
def _register_ssh_key_with_server(public_key: str, target_name: str | None = None) -> bool:
    from nexus.cli import api_client

    print(colored("\nRegistering SSH key with remote server...", "cyan"))
    try:
        result = api_client.register_ssh_key(public_key, target_name=target_name)
        if result.get("status") == "exists":
            print(colored("SSH key already registered", "yellow"))
        else:
            print(colored("SSH key registered successfully", "green"))
        return True
    except Exception as e:
        print(colored(f"Failed to register SSH key: {e}", "red"))
        print(colored("\nYou may need to manually add this key to the server:", "yellow"))
        print(public_key)
        return False
```

#### B. Replace setup_remote_server() with add_target() (~80 lines)

**Lines 324-356 become:**

```python
def add_target() -> None:
    import requests
    from termcolor import colored

    print(colored("\nAdd Target Server", "blue", attrs=["bold"]))
    print("Configure CLI to connect to a remote Nexus server.")

    try:
        cfg = config.load_config()
    except Exception:
        config.create_default_config()
        cfg = config.load_config()

    host = utils.get_user_input("Remote server hostname", required=True)
    port = int(utils.get_user_input("Remote server port", default="54323"))
    api_token = utils.get_user_input("API token", required=True, mask_input=True)
    protocol = "https"

    print(colored("\n🔍 Connecting to server...", "cyan"))

    cert_path = None
    try:
        cert_path = _download_server_certificate(host, port)
    except Exception as e:
        print(colored(f"Failed to download certificate: {e}", "red"))
        return

    try:
        url = f"{protocol}://{host}:{port}/v1/server/status"
        headers = {"Authorization": f"Bearer {api_token}"}
        verify = str(cert_path) if cert_path else False

        response = requests.get(url, headers=headers, verify=verify, timeout=10)
        response.raise_for_status()
        status = response.json()

        server_name = status.get("node_name")
        if not server_name:
            raise ValueError("Server did not return a node_name")

        print(colored(f"✓ Connected to: {server_name}", "green"))

    except Exception as e:
        print(colored(f"Failed to connect to server: {e}", "red"))
        if cert_path and cert_path.exists():
            cert_path.unlink()
        return

    if server_name in cfg.targets:
        print(colored(f"Target '{server_name}' already exists", "yellow"))
        if not utils.ask_yes_no("Overwrite existing target?"):
            return

    cfg.targets[server_name] = config.TargetConfig(
        host=host,
        port=port,
        protocol=protocol,
        api_token=api_token
    )

    if not cfg.default_target:
        cfg.default_target = server_name

    config.save_config(cfg)

    ssh_key_path = _generate_ssh_key(host, port)
    pub_key_path = ssh_key_path.with_suffix(".pub")
    public_key = pub_key_path.read_text().strip()

    _register_ssh_key_with_server(public_key, server_name)

    print(colored(f"\n✓ Target '{server_name}' configured", "green", attrs=["bold"]))
    print(f"Configuration saved to: {config.get_config_path()}")
```

#### C. Add management functions (~56 lines)

**list_targets():**
```python
def list_targets() -> None:
    cfg = config.load_config()
    default = cfg.default_target or "local"

    print(colored("Targets:", "blue", attrs=["bold"]))
    print(f"{'* ' if default == 'local' else '  '}local (http://localhost:54323)")

    for name, target in cfg.targets.items():
        marker = "* " if name == default else "  "
        print(f"{marker}{name} ({target.protocol}://{target.host}:{target.port})")
```

**set_default_target():**
```python
def set_default_target(target_name: str) -> None:
    cfg = config.load_config()

    if target_name != "local" and target_name not in cfg.targets:
        print(colored(f"Target '{target_name}' not found", "red"))
        print(colored("Use 'nx targets list' to see available targets", "yellow"))
        return

    cfg = tp.cast(config.NexusCliConfig, cfg.copy(update={"default_target": target_name}))
    config.save_config(cfg)
    print(colored(f"✓ Default target: {target_name}", "green"))
```

**remove_target():**
```python
def remove_target(target_name: str) -> None:
    cfg = config.load_config()

    if target_name not in cfg.targets:
        print(colored(f"Target '{target_name}' not found", "red"))
        return

    if not utils.ask_yes_no(f"Remove target '{target_name}'?"):
        print(colored("Operation cancelled.", "yellow"))
        return

    del cfg.targets[target_name]

    if cfg.default_target == target_name:
        cfg.default_target = None

    config.save_config(cfg)
    print(colored(f"✓ Removed target '{target_name}'", "green"))
    print(colored(f"Note: SSH keys and certificates in ~/.ssh and ~/.nexus were not deleted", "yellow"))
```

---

### 4. **main.py** (425 → ~507 lines, +82 LOC)

#### A. Add global --target flag (after parser creation, line ~200)
```python
parser.add_argument("--target", "-t", help="Target server (name or 'local')")
```

#### B. Add targets subcommand (after line 192, ~18 lines)
```python
targets_parser = subparsers.add_parser("targets", help="Manage target servers")
targets_subs = targets_parser.add_subparsers(dest="targets_command", required=True)

add_p = targets_subs.add_parser("add", help="Add target server")

targets_subs.add_parser("list", help="List all targets")

default_p = targets_subs.add_parser("default", help="Set default target")
default_p.add_argument("name", help="Target name or 'local'")

remove_p = targets_subs.add_parser("remove", help="Remove a target")
remove_p.add_argument("name", help="Target name to remove")
```

#### C. Add targets to get_command_handlers() (line ~216)
```python
def get_command_handlers(args, cfg: NexusCliConfig, parser: argparse.ArgumentParser):
    return {
        "config": lambda: handle_config(args, cfg),
        "env": lambda: handle_env(args),
        "jobrc": lambda: handle_jobrc(args),
        "setup": lambda: handle_setup(args),
        "targets": lambda: handle_targets(args),  # NEW
        "version": lambda: show_version(),
        "help": lambda: parser.print_help(),
    }
```

#### D. Modify get_api_command_handlers() (lines 226-271)

Add at the top:
```python
def get_api_command_handlers(args, cfg: NexusCliConfig):
    target_name = getattr(args, 'target', None)
    # ... rest of handlers, passing target_name to all job functions
```

All 13 handlers get `target_name=target_name` added.

#### E. Add handle_targets() (~15 lines)
```python
def handle_targets(args) -> None:
    if args.targets_command == "add":
        setup.add_target()
    elif args.targets_command == "list":
        setup.list_targets()
    elif args.targets_command == "default":
        setup.set_default_target(args.name)
    elif args.targets_command == "remove":
        setup.remove_target(args.name)
```

---

### 5. **jobs.py** (1207 → ~1242 lines, +35 LOC)

#### A. Add target_name parameter to 13 functions

Add `target_name: str | None = None` to:
1. `run_job` (line 12)
2. `add_jobs` (line 165)
3. `show_queue` (line 312)
4. `show_history` (line 350)
5. `kill_jobs` (line 430)
6. `remove_jobs` (line 590)
7. `view_logs` (line 681)
8. `show_health` (line 748)
9. `edit_job_command` (line 822)
10. `get_job_info` (line 872)
11. `handle_blacklist` (line 975)
12. `print_status` (line 1003)
13. `attach_to_job` (line 1078)

#### B. Pass target_name to ~35 api_client calls

**Pattern:**
```python
# Before:
health = api_client.get_detailed_health(refresh=False)

# After:
health = api_client.get_detailed_health(refresh=False, target_name=target_name)
```

#### C. Update attach_to_job() SSH logic (lines 1136-1180, ~50 lines)

**Replace lines 1136-1165:**
```python
active_name, target_cfg = config.get_active_target(target_name)

if target_cfg is None:
    current_user_exit_code = os.system(f"screen -r {screen_session_name}")

    if current_user_exit_code != 0:
        exit_code = os.system(f"sudo -u nexus screen -r {screen_session_name}")

        if exit_code != 0:
            print(colored("Screen session not found. Available sessions:", "yellow"))
            os.system("screen -ls")
            print(colored("\nTroubleshooting tips:", "yellow"))
            print("  1. Verify that the job is still running and the session name is correct.")
            print("  2. Check if you have the proper permissions to access the screen session.")
            print(f"  3. You can always view job logs with: nx logs {job_id}")
            return
else:
    import subprocess

    ssh_key = config.get_ssh_key_path(target_cfg.host, target_cfg.port)
    if not ssh_key.exists():
        print(colored(f"SSH key not found for target '{active_name}'", "red"))
        print(colored(f"Run: nx targets add", "yellow"))
        return

    result = subprocess.run(
        [
            "ssh",
            "-i",
            str(ssh_key),
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"nexus@{target_cfg.host}",
            "screen",
            "-r",
            screen_session_name,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(colored(f"\nSSH attach failed (exit {result.returncode})", "red"))
        if result.stderr:
            print(colored(result.stderr.strip()[:150], "red"))
        print(colored(f"View logs: nx logs {job_id} --target {active_name}", "yellow"))
        return
```

---

## Implementation Phases

### Phase 1: Config Foundation (1 hour)
- [ ] Add `TargetConfig` to config.py
- [ ] Update `NexusCliConfig` with targets dict
- [ ] Add `get_active_target()` function
- [ ] Update path helpers to take host/port instead of target_name
- [ ] Implement config-only migration logic (no file operations)
- [ ] **Test:** Load config with/without host field

### Phase 2: API Client (1 hour)
- [ ] Rewrite 3 helper functions
- [ ] Add `target_name` param to 15 API functions
- [ ] **Test:** API calls work with target_name param

### Phase 3: Setup & Management (1.5 hours)
- [ ] Update 3 helper functions in setup.py
- [ ] Implement `add_target()` with auto-fetch server name
- [ ] Add `list_targets()`, `set_default_target()`, `remove_target()`
- [ ] **Test:** Add target, list, set default, remove

### Phase 4: CLI Integration (1 hour)
- [ ] Add `--target`/`-t` global flag
- [ ] Add `targets` subcommand
- [ ] Modify command handlers to pass target_name
- [ ] Add `handle_targets()` function
- [ ] **Test:** Use --target with various commands

### Phase 5: Jobs & SSH (1 hour)
- [ ] Add `target_name` param to 13 job functions
- [ ] Pass `target_name` to ~35 api_client calls
- [ ] Update SSH attachment logic in `attach_to_job()`
- [ ] **Test:** Attach to local job, attach to remote job

### Phase 6: Integration Testing (1 hour)
- [ ] Test migration from feature branch remote config
- [ ] Test master branch upgrade (no migration)
- [ ] Test multi-target workflow
- [ ] Test `--target local` explicit targeting
- [ ] Test SSH attachment to multiple targets
- [ ] Update version to 0.6.0
- [ ] Commit and push

---

## Files Modified Summary

| File | Current | New | Change | Key Changes |
|------|---------|-----|--------|-------------|
| config.py | 87 | ~155 | +68 | TargetConfig, get_active_target(), host:port paths, config-only migration |
| api_client.py | 248 | ~268 | +20 | Rewrite 3 helpers, add target_name to 15 funcs |
| setup.py | 361 | ~449 | +88 | Auto-fetch server name, 3 mgmt funcs |
| main.py | 425 | ~507 | +82 | --target flag, targets subcommand |
| jobs.py | 1207 | ~1242 | +35 | target_name params, SSH logic |
| **TOTAL** | **2328** | **2588** | **+260** | |

---

## Bulletproof Checklist

### Design Integrity
- [x] Files named by server identity (host:port), not target name
- [x] Server name auto-fetched from API, not user-provided
- [x] Migration doesn't rely on server being available
- [x] No duplication of SSH keys/certs for same server
- [x] Target renaming doesn't break file references

### Minimal Complexity
- [x] Leverages existing config.load_config() pattern
- [x] Reuses existing helper function abstraction
- [x] No changes to server code
- [x] No changes to job execution logic
- [x] Migration only triggers when actually needed (feature branch remote)
- [x] Pydantic defaults handle master branch upgrade

### Maximum Integration
- [x] Follows existing pattern of config loading per function call
- [x] Uses same error handling decorators
- [x] Maintains same CLI structure (subcommands, flags)
- [x] Preserves all existing functionality
- [x] Backwards compatible (auto-migration for feature branch)

### Edge Cases Handled
- [x] IPv6 addresses in filenames (sanitized)
- [x] Server down during migration (no API calls)
- [x] Duplicate target names (checks before adding)
- [x] Missing certificates (file existence checks)
- [x] Missing SSH keys (file existence checks)
- [x] Invalid target names (validation in get_active_target)
- [x] Failed API connections (error handling)
- [x] Existing target overwrite protection
- [x] Master branch users (no migration, Pydantic defaults)

---

## Migration Details

### Master Branch Users (99% of users)
**Config before:**
```toml
port = 54323
user = "alice"
```

**What happens:**
1. No `host` field → migration doesn't trigger
2. Pydantic fills defaults: `targets={}`, `default_target=None`
3. User experience: Seamless, works exactly as before

### Feature Branch Users (dev only - you!)
**Config before:**
```toml
host = "gpu-cluster-1.example.com"
protocol = "https"
port = 54323
api_token = "secret-token-123"
user = "alice"
```

**What happens:**
1. `host` field exists and is not localhost → migration triggers
2. Config migrated to single target named "gpu-cluster-1.example.com"
3. User sees: "Migrating config to target 'gpu-cluster-1.example.com'"
4. User experience: Quick config update, SSH keys/certs created fresh by `nx targets add` if needed

**Note:** No file renaming needed - master branch had no remote files to begin with!

---

## User Experience Examples

### Adding a target
```bash
$ nx targets add
  Remote server hostname: gpu-cluster-1.example.com
  Remote server port [54323]:
  API token: ***********

  🔍 Connecting to server...
  🔒 Downloading certificate...
  ✓ Connected to: gpu-cluster-1

  🔐 Generating SSH key...
  🔐 Registering SSH key...
  ✓ SSH key registered successfully

  ✓ Target 'gpu-cluster-1' configured
  Configuration saved to: /home/user/.nexus/config.toml
```

### Using targets
```bash
# Uses default
$ nx run python train.py

# Target specific server
$ nx run --target gpu-cluster-2 python test.py

# Explicit local
$ nx run --target local python debug.py

# All commands support --target
$ nx queue --target gpu-cluster-1
$ nx logs --target gpu-cluster-2 abc123
$ nx attach --target gpu-cluster-1 def456
```

### Managing targets
```bash
$ nx targets list
  * gpu-cluster-1 (https://gpu-cluster-1.example.com:54323)
    gpu-cluster-2 (https://gpu-cluster-2.example.com:54323)
    local (http://localhost:54323)

$ nx targets default gpu-cluster-2
  ✓ Default target: gpu-cluster-2

$ nx targets remove gpu-cluster-1
  Remove target 'gpu-cluster-1'? [y/N]: y
  ✓ Removed target 'gpu-cluster-1'
```
