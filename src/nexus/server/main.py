import dataclasses as dc
import pathlib as pl
import socket

import uvicorn

from nexus.server.api import app
from nexus.server.installation import setup

__all__ = ["main"]


def _run_server(server_dir: pl.Path | None) -> None:
    ctx = setup.initialize_context(server_dir)

    if ctx.config.server_dir is not None and ctx.config.api_token:
        key_path, cert_path = setup.generate_self_signed_cert(ctx.config.server_dir)

        updated_config = ctx.config.model_copy(
            update={
                "ssl_keyfile": str(key_path),
                "ssl_certfile": str(cert_path),
            }
        )
        ctx = dc.replace(ctx, config=updated_config)

        api_app = app.create_app(ctx)

        print("🔒 SSL: enabled")
        print("🔑 Token: configured")
        print(f"🌐 https://{socket.gethostname()}:{ctx.config.port} (0.0.0.0:{ctx.config.port})")
        print()
        host = "0.0.0.0"
        ssl_keyfile = str(key_path)
        ssl_certfile = str(cert_path)
    else:
        api_app = app.create_app(ctx)
        if ctx.config.server_dir is not None:
            print("⚠️  No API token - LOCAL ONLY")
            print("\nTo enable remote: Install server with nexus-server install")
            print(f"\nServer: http://localhost:{ctx.config.port}\n")
        else:
            setup.display_config(ctx.config)
        host = "localhost"
        ssl_keyfile = None
        ssl_certfile = None

    uvicorn.run(
        api_app,
        host=host,
        port=ctx.config.port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
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
