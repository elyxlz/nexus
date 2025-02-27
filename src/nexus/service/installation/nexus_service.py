"""Systemd service file template for Nexus GPU Job Management Service."""

UNIT_SECTION = """[Unit]
Description=Nexus GPU Job Management Service
After=network.target
"""

SERVICE_SECTION = """[Service]
Type=simple
User=nexus
Group=nexus
WorkingDirectory=/home/nexus
ExecStart=/usr/local/bin/nexus-service
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/nexus_service/env
"""

INSTALL_SECTION = """[Install]
WantedBy=multi-user.target
"""

SERVICE_FILE_CONTENT = UNIT_SECTION + SERVICE_SECTION + INSTALL_SECTION

def get_service_file_content() -> str:
    """Return the content of the service file."""
    return SERVICE_FILE_CONTENT