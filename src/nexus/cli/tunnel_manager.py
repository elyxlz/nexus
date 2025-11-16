import os
import pathlib as pl
import socket
import subprocess
import tempfile
import time

from nexus.cli import config


class SSHTunnelError(Exception):
    pass


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


def _wait_for_tunnel(local_port: int, timeout: float = 10.0) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", local_port))
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(0.1)
    return False


def _get_tunnels_dir() -> pl.Path:
    tunnels_dir = pl.Path.home() / ".nexus" / "tunnels"
    tunnels_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return tunnels_dir


def _sanitize_target_name(target_name: str) -> str:
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    sanitized = "".join(c if c in safe_chars else "_" for c in target_name)
    if not sanitized or sanitized in (".", ".."):
        sanitized = "default"
    return sanitized[:64]


def _get_socket_path(target_name: str) -> pl.Path:
    safe_name = _sanitize_target_name(target_name)
    return _get_tunnels_dir() / f"{safe_name}.sock"


def _get_port_path(target_name: str) -> pl.Path:
    safe_name = _sanitize_target_name(target_name)
    return _get_tunnels_dir() / f"{safe_name}.port"


def _read_port_file(target_name: str) -> int | None:
    port_path = _get_port_path(target_name)
    if not port_path.exists():
        return None
    try:
        content = port_path.read_text().strip()
        return int(content)
    except (ValueError, OSError):
        return None


def _write_port_file(target_name: str, port: int) -> None:
    port_path = _get_port_path(target_name)
    tunnels_dir = port_path.parent

    fd, temp_path = tempfile.mkstemp(dir=tunnels_dir, prefix=".port_", suffix=".tmp")
    closed = False
    try:
        os.write(fd, f"{port}\n".encode())
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.chmod(temp_path, 0o600)
        os.rename(temp_path, port_path)
    finally:
        if not closed:
            os.close(fd)
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _remove_port_file(target_name: str) -> None:
    port_path = _get_port_path(target_name)
    try:
        if port_path.exists():
            port_path.unlink()
    except OSError:
        pass


def _check_control_socket(target_name: str) -> bool:
    socket_path = _get_socket_path(target_name)
    if not socket_path.exists():
        return False

    try:
        result = subprocess.run(
            ["ssh", "-S", str(socket_path), "-O", "check", "dummy"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _stop_control_master(target_name: str) -> bool:
    socket_path = _get_socket_path(target_name)
    if not socket_path.exists():
        _remove_port_file(target_name)
        return False

    try:
        result = subprocess.run(
            ["ssh", "-S", str(socket_path), "-O", "exit", "dummy"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        success = result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        success = False

    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            pass

    _remove_port_file(target_name)
    return success


def _start_control_master(target_name: str, target_cfg: config.TargetConfig) -> int:
    socket_path = _get_socket_path(target_name)
    local_port = _find_free_port()

    ssh_cmd = [
        "ssh",
        "-M",
        "-S",
        str(socket_path),
        "-fN",
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
        "-o",
        "ControlPersist=yes",
        f"{target_cfg.ssh_user}@{target_cfg.host}",
    ]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise SSHTunnelError("SSH connection timed out after 30 seconds\nHint: Check network connectivity")

    if result.returncode != 0:
        raise SSHTunnelError(
            f"Failed to start SSH tunnel\n"
            f"Error: {result.stderr.strip()}\n"
            f"Hint: Verify SSH access with: ssh {target_cfg.ssh_user}@{target_cfg.host} echo ok"
        )

    if not _wait_for_tunnel(local_port, timeout=10.0):
        _stop_control_master(target_name)
        raise SSHTunnelError(
            f"SSH tunnel started but port {local_port} not responding\n"
            f"Hint: Check that the Nexus server is running on {target_cfg.host}:{target_cfg.port}"
        )

    _write_port_file(target_name, local_port)
    return local_port


def _get_tunnel_port(target_name: str) -> int | None:
    if not _check_control_socket(target_name):
        _remove_port_file(target_name)
        socket_path = _get_socket_path(target_name)
        if socket_path.exists():
            try:
                socket_path.unlink()
            except OSError:
                pass
        return None

    local_port = _read_port_file(target_name)
    if local_port is None:
        _stop_control_master(target_name)
        return None

    if not _wait_for_tunnel(local_port, timeout=1.0):
        _stop_control_master(target_name)
        return None

    return local_port


def get_or_create_tunnel(target_name: str) -> int:
    _, target_cfg = config.get_active_target(target_name)

    if target_cfg is None or target_cfg.host in ("localhost", "127.0.0.1"):
        return target_cfg.port if target_cfg else 54323

    port = _get_tunnel_port(target_name)
    if port is not None:
        return port

    if target_cfg is None:
        raise ValueError(f"Target '{target_name}' is local, no tunnel needed")

    _stop_control_master(target_name)
    return _start_control_master(target_name, target_cfg)
