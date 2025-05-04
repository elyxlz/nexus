#!/usr/bin/env python3
import os
import subprocess
import sys

NEXUS_RUN_SCRIPT = """#!/bin/bash
set -e

# Start rqlite with the right options
if [ -f /etc/nexus_server/rqlite/join.env ]; then
    source /etc/nexus_server/rqlite/join.env
    echo "Starting rqlite node in cluster mode..."
    rqlited -node-id "$(hostname)" -join $JOIN_FLAGS -auth /etc/nexus_server/auth.conf /var/lib/rqlite &
else
    echo "Starting rqlite node in standalone mode..."
    rqlited -node-id "$(hostname)" -auth /etc/nexus_server/auth.conf /var/lib/rqlite &
fi

RQLITE_PID=$!
echo "rqlite started with PID $RQLITE_PID"

# Start nexus-server and make sure it's in foreground
echo "Starting nexus-server..."
exec /usr/local/bin/nexus-server
"""

def install_wrapper():
    wrapper_path = "/usr/local/bin/nexus-run.sh"
    with open(wrapper_path, "w") as f:
        f.write(NEXUS_RUN_SCRIPT)
    
    os.chmod(wrapper_path, 0o755)
    return wrapper_path

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        path = install_wrapper()
        print(f"Installed wrapper script to {path}")
        return 0
    
    # Execute directly if no args provided
    try:
        process = subprocess.run(["/bin/bash", "-c", NEXUS_RUN_SCRIPT])
        return process.returncode
    except KeyboardInterrupt:
        return 130

if __name__ == "__main__":
    sys.exit(main())