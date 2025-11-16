import contextlib
import socket
import subprocess
import time
import typing as tp

from termcolor import colored


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
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    return False


@contextlib.contextmanager
def ssh_tunnel(host: str, remote_port: int, ssh_user: str) -> tp.Iterator[int]:
    local_port = _find_free_port()

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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not _wait_for_tunnel(local_port, timeout=15.0):
            returncode = process.poll()
            if returncode is not None:
                stderr_output = process.stderr.read().decode() if process.stderr else ""
                raise SSHTunnelError(
                    f"SSH tunnel failed to start (exit code {returncode})\n"
                    f"Command: {' '.join(ssh_cmd)}\n"
                    f"Error: {stderr_output.strip()}\n\n"
                    f"{colored('Ensure your SSH key is in ~/.ssh/authorized_keys on the remote host', 'yellow')}"
                )
            else:
                process.terminate()
                process.wait(timeout=5)
                raise SSHTunnelError(
                    f"SSH tunnel connection timed out after 15 seconds\n"
                    f"Command: {' '.join(ssh_cmd)}\n\n"
                    f"{colored('Ensure your SSH key is in ~/.ssh/authorized_keys on the remote host', 'yellow')}"
                )

        yield local_port

    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
