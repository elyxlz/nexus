import pathlib as pl

import uvicorn

from nexus.server.api import app
from nexus.server.installation import setup

__all__ = ["main"]


def _run_server(server_dir: pl.Path | None) -> None:
    ctx = setup.initialize_context(server_dir)
    api_app = app.create_app(ctx)

    print(f"🖥️  Node: {ctx.config.node_name}")
    print("🔒 Auth: SSH tunnel (remote access via SSH port forwarding)")
    print(f"🌐 Server: http://127.0.0.1:{ctx.config.port} (localhost only)")
    print()

    uvicorn.run(
        api_app,
        host="127.0.0.1",
        port=ctx.config.port,
    )


def main() -> None:
    parser = setup.create_argument_parser()
    args = parser.parse_args()

    setup.handle_version_check()

    if setup.handle_command(args):
        return

    server_dir = setup.get_server_directory()

    if server_dir is None:
        setup.prompt_installation_mode()
        server_dir = setup.get_server_directory()
        if setup.get_installation_info().install_mode == "system":
            print("Server installed and running via systemd. Exiting.")
            return

    _run_server(server_dir)


if __name__ == "__main__":
    main()
