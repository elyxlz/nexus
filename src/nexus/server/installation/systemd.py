"""Systemd service file template for Nexus GPU Job Management Server."""

UNIT_SECTION = """[Unit]
Description=Nexus GPU Job Management Server
After=network.target
"""

SERVICE_SECTION = """[Service]
Type=simple
WorkingDirectory=/home/nexus
ExecStart=/bin/su - nexus -c "/usr/local/bin/nexus-server"
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
"""

INSTALL_SECTION = """[Install]
WantedBy=multi-user.target
"""

SERVICE_FILE_CONTENT = UNIT_SECTION + SERVICE_SECTION + INSTALL_SECTION


def get_service_file_content() -> str:
    return SERVICE_FILE_CONTENT
