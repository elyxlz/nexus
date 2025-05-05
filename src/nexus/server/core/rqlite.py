import atexit
import subprocess
import signal
import socket
import sys
import tempfile
import time
import pathlib as pl

from pyrqlite import dbapi2 as dbapi
from nexus.server.core import config
from nexus.server.utils import logger

RQLITE_JOIN_ENV_FILE = pl.Path("/etc/nexus_server/rqlite/join.env")
RQLITE_AUTH_FILE = pl.Path("/etc/nexus_server/auth.conf")
RQLITE_DATA_DIR = pl.Path("/var/lib/rqlite")

rqlite_process = None


def connect(cfg: config.NexusServerConfig, scheduler_mode: bool = False):
    """Connect to a rqlite database using the configuration object
    
    Args:
        cfg: The server configuration
        scheduler_mode: Whether this connection is for the scheduler.
                       If True, uses linearizable consistency for immediate cross-cluster visibility.
    """
    host, port = cfg.rqlite_host.split(":")
    consistency = "linearizable" if scheduler_mode else "weak"
    return connect_with_params(host=host, port=int(port), api_key=cfg.api_key, consistency=consistency)


def dict_factory(cursor, row):
    """Factory function to convert database rows to dictionaries more efficiently"""
    return {c[0]: row[i] for i, c in enumerate(cursor.description)}


def connect_with_params(host: str, port: int, api_key: str, consistency: str = "weak"):
    """Connect to a rqlite database using explicit parameters"""
    conn = dbapi.connect(
        host=host,
        port=port,
        user="nexus",
        password=api_key,
        https=False,
        verify_https=False,
        consistency=consistency,
    )
    conn.row_factory = dict_factory
    return conn


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def write_auth_config(auth_file: pl.Path, api_key: str) -> None:
    """Write authentication config file for rqlite"""
    auth_content = f"nexus:{api_key}"
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(auth_content)
    auth_file.chmod(0o600)


def cleanup_rqlite() -> None:
    """Terminate rqlite process when nexus-server exits"""
    global rqlite_process
    if rqlite_process is not None:
        logger.info("Stopping rqlite process...")
        try:
            rqlite_process.terminate()
            rqlite_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("rqlite did not terminate gracefully, forcing exit")
            rqlite_process.kill()
        except Exception as e:
            logger.error(f"Error terminating rqlite: {e}")


def setup_rqlite(cfg: config.NexusServerConfig) -> None:
    """Start rqlite and prepare configuration"""
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

    # Create temporary or permanent auth file
    if cfg.server_dir is None:
        # For testing/ephemeral mode, use temporary file
        auth_file = pl.Path(tempfile.mktemp(prefix="rqlite_auth_"))
        data_dir = pl.Path(tempfile.mkdtemp(prefix="rqlite_data_"))
    else:
        # For production mode, use system paths
        auth_file = RQLITE_AUTH_FILE
        data_dir = RQLITE_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)

    write_auth_config(auth_file, cfg.api_key)

    # Determine if we're joining or bootstrapping
    cmd = ["rqlited", "-node-id", socket.gethostname(), "-auth", str(auth_file)]

    join_flags = []
    if RQLITE_JOIN_ENV_FILE.exists():
        with open(RQLITE_JOIN_ENV_FILE) as f:
            for line in f:
                if line.startswith("JOIN_FLAGS="):
                    join_part = line.strip().split("=", 1)[1]
                    # Remove surrounding quotes if present
                    join_part = join_part.strip("\"'")
                    join_flags = ["-join", join_part]
                    break

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
