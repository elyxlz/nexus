import contextlib
import socket
import subprocess
import time
import typing as tp


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


def _cleanup_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _attempt_tunnel(host: str, remote_port: int, ssh_user: str, local_port: int) -> subprocess.Popen:
    ssh_cmd = [
        "ssh",
        "-N",
        "-L",
        f"{local_port}:127.0.0.1:{remote_port}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=60",
        "-o",
        "ExitOnForwardFailure=yes",
        f"{ssh_user}@{host}",
    ]

    process = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_tunnel(local_port, timeout=15.0):
        returncode = process.poll()
        stderr_output = ""
        if process.stderr:
            try:
                stderr_output = process.stderr.read(4096).decode()
            except Exception:
                pass

        _cleanup_process(process)

        if returncode is not None:
            raise SSHTunnelError(
                f"SSH tunnel failed to start (exit code {returncode})\n"
                f"Error: {stderr_output.strip()}\n"
                f"Hint: Verify SSH access with: ssh {ssh_user}@{host} echo ok"
            )
        else:
            raise SSHTunnelError(
                f"SSH tunnel connection timed out\nHint: Check network connectivity and that sshd is running on {host}"
            )

    return process


@contextlib.contextmanager
def ssh_tunnel(host: str, remote_port: int, ssh_user: str, max_retries: int = 3) -> tp.Iterator[int]:
    last_error: SSHTunnelError | None = None
    process: subprocess.Popen | None = None
    local_port: int = 0

    for attempt in range(max_retries):
        local_port = _find_free_port()

        try:
            process = _attempt_tunnel(host, remote_port, ssh_user, local_port)
            break
        except SSHTunnelError as e:
            last_error = e
            error_str = str(e)
            if "Address already in use" in error_str and attempt < max_retries - 1:
                time.sleep(0.1)
                continue
            raise

    if process is None:
        if last_error:
            raise last_error
        raise SSHTunnelError("Failed to establish SSH tunnel")

    try:
        yield local_port
    finally:
        _cleanup_process(process)
