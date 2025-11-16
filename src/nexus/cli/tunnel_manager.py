import json
import os
import pathlib as pl
import signal
import subprocess
import time
import typing as tp

from termcolor import colored

from nexus.cli import config
from nexus.cli.ssh_tunnel import SSHTunnelError, _cleanup_process, _find_free_port, _wait_for_tunnel


def _get_tunnels_dir() -> pl.Path:
    tunnels_dir = pl.Path.home() / ".nexus" / "tunnels"
    tunnels_dir.mkdir(parents=True, exist_ok=True)
    return tunnels_dir


def _get_tunnel_state_path(target_name: str) -> pl.Path:
    safe_name = target_name.replace("/", "_").replace("\\", "_")
    return _get_tunnels_dir() / f"{safe_name}.json"


def _read_tunnel_state(target_name: str) -> dict | None:
    state_path = _get_tunnel_state_path(target_name)
    if not state_path.exists():
        return None
    try:
        with state_path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _write_tunnel_state(target_name: str, state: dict) -> None:
    state_path = _get_tunnel_state_path(target_name)
    with state_path.open("w") as f:
        json.dump(state, f, indent=2)


def _remove_tunnel_state(target_name: str) -> None:
    state_path = _get_tunnel_state_path(target_name)
    if state_path.exists():
        state_path.unlink()


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _check_tunnel_health(local_port: int) -> bool:
    return _wait_for_tunnel(local_port, timeout=1.0)


def _start_tunnel_daemon(target_cfg: config.TargetConfig) -> tuple[int, int]:
    local_port = _find_free_port()

    ssh_cmd = [
        "ssh",
        "-N",
        "-f",
        "-L",
        f"{local_port}:127.0.0.1:{target_cfg.port}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=60",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        f"{target_cfg.ssh_user}@{target_cfg.host}",
    ]

    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        raise SSHTunnelError(
            f"Failed to start SSH tunnel daemon\n"
            f"Error: {result.stderr.strip()}\n"
            f"Hint: Verify SSH access with: ssh {target_cfg.ssh_user}@{target_cfg.host} echo ok"
        )

    if not _wait_for_tunnel(local_port, timeout=10.0):
        raise SSHTunnelError(
            f"SSH tunnel daemon started but port {local_port} not responding\n"
            f"Hint: Check network connectivity to {target_cfg.host}"
        )

    pgrep_result = subprocess.run(
        ["pgrep", "-f", f"ssh.*-L.*{local_port}:127.0.0.1:{target_cfg.port}"],
        capture_output=True,
        text=True,
    )

    if pgrep_result.returncode != 0 or not pgrep_result.stdout.strip():
        raise SSHTunnelError("Failed to find SSH tunnel process PID")

    pid = int(pgrep_result.stdout.strip().split("\n")[0])
    return pid, local_port


def start_tunnel(target_name: str) -> int:
    _, target_cfg = config.get_active_target(target_name)

    if target_cfg is None:
        raise ValueError(f"Target '{target_name}' is local, no tunnel needed")

    if target_cfg.host in ("localhost", "127.0.0.1"):
        raise ValueError(f"Target '{target_name}' is localhost, no tunnel needed")

    stop_tunnel(target_name)

    pid, local_port = _start_tunnel_daemon(target_cfg)

    state = {
        "pid": pid,
        "local_port": local_port,
        "host": target_cfg.host,
        "remote_port": target_cfg.port,
        "ssh_user": target_cfg.ssh_user,
        "started_at": time.time(),
    }
    _write_tunnel_state(target_name, state)

    return local_port


def stop_tunnel(target_name: str) -> bool:
    state = _read_tunnel_state(target_name)
    if state is None:
        return False

    pid = state.get("pid")
    if pid and _is_process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _is_process_alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    _remove_tunnel_state(target_name)
    return True


def get_tunnel_port(target_name: str) -> int | None:
    state = _read_tunnel_state(target_name)
    if state is None:
        return None

    pid = state.get("pid")
    local_port = state.get("local_port")

    if not pid or not local_port:
        _remove_tunnel_state(target_name)
        return None

    if not _is_process_alive(pid):
        _remove_tunnel_state(target_name)
        return None

    if not _check_tunnel_health(local_port):
        stop_tunnel(target_name)
        return None

    return local_port


def get_or_create_tunnel(target_name: str) -> int:
    _, target_cfg = config.get_active_target(target_name)

    if target_cfg is None or target_cfg.host in ("localhost", "127.0.0.1"):
        return target_cfg.port if target_cfg else 54323

    port = get_tunnel_port(target_name)
    if port is not None:
        return port

    return start_tunnel(target_name)


def get_tunnel_status(target_name: str) -> dict:
    state = _read_tunnel_state(target_name)
    if state is None:
        return {"status": "not_running", "target": target_name}

    pid = state.get("pid")
    local_port = state.get("local_port")

    if not pid or not _is_process_alive(pid):
        _remove_tunnel_state(target_name)
        return {"status": "dead", "target": target_name}

    if not local_port or not _check_tunnel_health(local_port):
        return {
            "status": "unhealthy",
            "target": target_name,
            "pid": pid,
            "local_port": local_port,
        }

    uptime = time.time() - state.get("started_at", 0)
    return {
        "status": "healthy",
        "target": target_name,
        "pid": pid,
        "local_port": local_port,
        "host": state.get("host"),
        "remote_port": state.get("remote_port"),
        "uptime_seconds": uptime,
    }


def list_all_tunnels() -> list[dict]:
    tunnels_dir = _get_tunnels_dir()
    results = []

    for state_file in tunnels_dir.glob("*.json"):
        target_name = state_file.stem
        status = get_tunnel_status(target_name)
        results.append(status)

    return results
