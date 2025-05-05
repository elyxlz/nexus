#!/usr/bin/env python3
import os
import subprocess
import sys

NEXUS_RUN_SCRIPT = """#!/bin/bash
set -e

JOIN_OPTS=""
if [ -f /etc/nexus_server/auth.conf ]; then
    JOIN_OPTS="-join-as nexus -join-auth-file /etc/nexus_server/auth.conf"
fi

if [ -f /etc/nexus_server/rqlite/join.env ]; then
    source /etc/nexus_server/rqlite/join.env
    
    rqlited -node-id "$(hostname)" -join $JOIN_FLAGS ${JOIN_OPTS:+$JOIN_OPTS} -auth /etc/nexus_server/auth.conf /var/lib/rqlite &
else
    
    rqlited -node-id "$(hostname)" -auth /etc/nexus_server/auth.conf /var/lib/rqlite &
fi

RQLITE_PID=$!

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
