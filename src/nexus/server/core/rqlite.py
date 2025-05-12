import atexit
import subprocess
import signal
import socket
import sys
import tempfile
import time
import os
import platform
import tarfile
import shutil
import pathlib as pl
import requests

from pyrqlite import dbapi2 as dbapi
from nexus.server.core import config
from nexus.server.utils import logger

RQLITE_JOIN_ENV_FILE = pl.Path("/etc/nexus_server/rqlite/join.env")
RQLITE_AUTH_FILE = pl.Path("/etc/nexus_server/auth.conf")
RQLITE_DATA_DIR = pl.Path("/var/lib/rqlite")
RQLITE_BINARY_PATH = None

rqlite_process = None


def connect(cfg: config.NexusServerConfig, scheduler_mode: bool = False):
    """Connect to a rqlite database using the configuration object

    Args:
        cfg: The server configuration
        scheduler_mode: Whether this connection is for the scheduler.
                       If True, uses linearizable consistency for immediate cross-cluster visibility.
    """
    host, port = cfg.rqlite_host.split(":")
    return connect_with_params(host=host, port=int(port), api_key=cfg.api_key)


def dict_factory(cursor, row):
    """Factory function to convert database rows to dictionaries more efficiently"""
    return {c[0]: row[i] for i, c in enumerate(cursor.description)}


def connect_with_params(host: str, port: int, api_key: str):
    """Connect to a rqlite database using explicit parameters"""
    conn = dbapi.connect(
        host=host,
        port=port,
        user="nexus",
        password=api_key,
        scheme="http",  # Use http instead of https
    )
    # Note: pyrqlite doesn't support setting row_factory like sqlite3 does
    # It already returns dict-like Row objects that can be accessed by column name
    return conn


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def download_rqlite() -> pl.Path:
    """Download rqlite binary from GitHub releases if not already available.

    Returns:
        Path to the rqlite binary
    """
    global RQLITE_BINARY_PATH

    # If we've already downloaded it or found a system one, return that
    if RQLITE_BINARY_PATH and os.path.exists(RQLITE_BINARY_PATH):
        return pl.Path(RQLITE_BINARY_PATH)

    # First check if rqlited is in PATH
    try:
        which_result = subprocess.run(["which", "rqlited"], capture_output=True, text=True, check=False)
        if which_result.returncode == 0:
            binary_path = which_result.stdout.strip()
            RQLITE_BINARY_PATH = binary_path
            logger.info(f"Found system rqlited at {binary_path}")
            return pl.Path(binary_path)
    except Exception:
        pass

    # Determine system architecture
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Map architecture to rqlite release name
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}

    if machine in arch_map:
        arch = arch_map[machine]
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    # Create directory for downloaded binary
    cache_dir = pl.Path.home() / ".cache" / "nexus"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Set up paths
    version = "7.21.4"  # Latest stable version at time of writing
    url = f"https://github.com/rqlite/rqlite/releases/download/v{version}/rqlite-v{version}-{os_name}-{arch}.tar.gz"
    binary_dir = cache_dir / f"rqlite-{version}"
    tar_path = binary_dir / "rqlite.tar.gz"

    # ── fast path ── a flattened binary already exists, just use it
    binary_path = binary_dir / "rqlited"
    if binary_path.is_file():
        RQLITE_BINARY_PATH = str(binary_path)
        logger.info("Using cached rqlited binary → %s", binary_path)
        return binary_path

    # Create directory for this version
    binary_dir.mkdir(parents=True, exist_ok=True)

    # Download tarball
    logger.info(f"Downloading rqlite from {url}")
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(tar_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # Unpack ONCE into a temporary folder, then move the binary to the
    # shared cache path; if another process wins the race we just reuse it.
    tmp_extract = binary_dir / "__tmp_extract__"
    tmp_extract.mkdir(exist_ok=True)
    try:
        with tarfile.open(tar_path) as tar:
            tar.extractall(path=tmp_extract)

        for root, _, files in os.walk(tmp_extract):
            if "rqlited" in files:
                src = pl.Path(root) / "rqlited"
                break
        else:
            raise RuntimeError("rqlited binary not found in tarball")

        try:
            src.replace(binary_path)           # atomic, first writer wins
        except FileExistsError:
            pass                               # already copied by another worker
        os.chmod(binary_path, 0o755)
    finally:
        shutil.rmtree(tmp_extract, ignore_errors=True)

    logger.info(f"Downloaded rqlited binary to {binary_path}")
    RQLITE_BINARY_PATH = str(binary_path)
    return binary_path


def write_auth_config(auth_file: pl.Path, api_key: str) -> None:
    """Write authentication config file for rqlite.

    The rqlite auth file is a JSON array of user objects.
    See: https://rqlite.io/docs/guides/security/
    """
    auth_content = f"""[
  {{
    "username": "nexus",
    "password": "{api_key}",
    "perms": ["all"]
  }}
]"""
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(auth_content)
    auth_file.chmod(0o600)


def cleanup_rqlite() -> None:
    """Terminate rqlite process when nexus-server exits"""
    global rqlite_process
    if rqlite_process is not None:
        try:
            # Avoid logging here since it may be called during process shutdown
            # when logging streams might be closed
            rqlite_process.terminate()
            rqlite_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rqlite_process.kill()
        except Exception:
            pass


def setup_rqlite(
    cfg: config.NexusServerConfig,
    auth_file_path: pl.Path = RQLITE_AUTH_FILE,
    data_dir_path: pl.Path = RQLITE_DATA_DIR,
    join_env_file_path: pl.Path = RQLITE_JOIN_ENV_FILE
) -> None:
    """Start rqlite and prepare configuration

    Args:
        cfg: Configuration for the Nexus server
        auth_file_path: Path to auth file, defaults to RQLITE_AUTH_FILE
        data_dir_path: Path to data directory, defaults to RQLITE_DATA_DIR
        join_env_file_path: Path to join env file, defaults to RQLITE_JOIN_ENV_FILE
    """
    global rqlite_process

    # Register cleanup handler
    atexit.register(cleanup_rqlite)

    # Handle SIGTERM to ensure clean shutdown with systemd
    def handle_sigterm(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Check if rqlite is already running on the expected port
    host, port_str = cfg.rqlite_host.split(":")
    port = int(port_str)

    if is_port_in_use(port):
        logger.info(f"rqlite already running on port {port}, skipping launch")
        return

    # For testing/ephemeral mode, use temporary files if explicit defaults are used
    if cfg.server_dir is None and auth_file_path == RQLITE_AUTH_FILE:
        auth_file = pl.Path(tempfile.mktemp(prefix="rqlite_auth_"))
    else:
        auth_file = auth_file_path

    if cfg.server_dir is None and data_dir_path == RQLITE_DATA_DIR:
        data_dir = pl.Path(tempfile.mkdtemp(prefix="rqlite_data_"))
    else:
        data_dir = data_dir_path
        data_dir.mkdir(parents=True, exist_ok=True)

    # Use the provided join env file path
    join_file = join_env_file_path

    # Write the auth configuration
    write_auth_config(auth_file, cfg.api_key)

    # Download or find rqlite binary
    rqlited_path = download_rqlite()

    # Determine if we're joining or bootstrapping
    cmd = [str(rqlited_path), "-node-id", socket.gethostname(), "-auth", str(auth_file)]

    # In test mode, use pre-configured ports for rqlite HTTP and Raft interfaces
    if cfg.server_dir is None:
        # Set explicit HTTP and Raft ports for testing to avoid conflicts
        cmd.extend(["-http-addr", f"{host}:{port}"])
        # Use port+1 for Raft communication to avoid conflicts
        cmd.extend(["-raft-addr", f"{host}:{port + 1}"])

    join_flags = []
    try:
        if join_file.exists():
            with open(join_file) as f:
                for line in f:
                    if line.startswith("JOIN_FLAGS="):
                        join_part = line.strip().split("=", 1)[1]
                        # Remove surrounding quotes if present
                        join_part = join_part.strip("\"'")
                        join_flags = ["-join", join_part]
                        break
    except (PermissionError, FileNotFoundError):
        # Skip if file doesn't exist or we don't have permission
        pass

    if join_flags:
        logger.info("Starting rqlite in cluster join mode")
        cmd.extend(join_flags)
    else:
        logger.info("Starting rqlite in standalone mode")

    # Add data directory as the last argument
    cmd.append(str(data_dir))

    # Start rqlite process
    logger.info(f"Launching rqlite: {' '.join(cmd)}")
    rqlite_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait briefly for rqlite to start
    time.sleep(2)

    # Check if process is still running
    if rqlite_process.poll() is not None:
        stdout, stderr = rqlite_process.communicate()
        raise RuntimeError(f"rqlite failed to start: {stderr}")

    logger.info(f"rqlite started with PID {rqlite_process.pid}")

    # Wait for port to become available
    retries = 10
    while retries > 0 and not is_port_in_use(port):
        logger.info(f"Waiting for rqlite to become available on port {port}...")
        time.sleep(1)
        retries -= 1

    if not is_port_in_use(port):
        logger.warning(f"rqlite did not bind to port {port} in the expected time")
    else:
        logger.info(f"rqlite is now available on port {port}")
