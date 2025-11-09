"""Systemd service file template for Nexus GPU Job Management Server."""

UNIT_SECTION = """[Unit]
Description=Nexus GPU Job Management Server
After=network.target
"""

INSTALL_SECTION = """[Install]
WantedBy=multi-user.target
"""


def build_service_section(exec_path: str) -> str:
    return f"""[Service]
Type=simple
User=nexus
Group=nexus
WorkingDirectory=/home/nexus
ExecStart={exec_path}
KillMode=process
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=nexus-server
Environment=PYTHONUNBUFFERED=1
LimitMEMLOCK=infinity
LimitNOFILE=65536
"""


def get_service_file_content(exec_path: str, sup_groups: list[str] | None = None) -> str:
    service_section = build_service_section(exec_path)
    content = UNIT_SECTION + service_section + INSTALL_SECTION

    if not sup_groups:
        return content

    content_lines = content.splitlines()
    new_lines = []

    for line in content_lines:
        new_lines.append(line)
        if line.strip() == "[Service]":
            new_lines.append(f"SupplementaryGroups={' '.join(sup_groups)}")

    return "\n".join(new_lines)
