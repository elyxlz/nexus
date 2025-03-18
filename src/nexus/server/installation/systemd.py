"""Systemd service file template for Nexus GPU Job Management Server."""

UNIT_SECTION = """[Unit]
Description=Nexus GPU Job Management Server
After=network.target
"""

SERVICE_SECTION = """[Service]
Type=simple
User=nexus
Group=nexus
WorkingDirectory=/home/nexus
ExecStart=/usr/local/bin/nexus-server
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
"""

INSTALL_SECTION = """[Install]
WantedBy=multi-user.target
"""

SERVICE_FILE_CONTENT = UNIT_SECTION + SERVICE_SECTION + INSTALL_SECTION


def get_service_file_content(sup_groups: list[str] | None = None) -> str:
    if not sup_groups:
        return SERVICE_FILE_CONTENT

    content_lines = SERVICE_FILE_CONTENT.splitlines()
    new_lines = []

    for line in content_lines:
        new_lines.append(line)
        if line.strip() == "[Service]":
            new_lines.append(f"SupplementaryGroups={' '.join(sup_groups)}")

    return "\n".join(new_lines)
