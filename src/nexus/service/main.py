import pathlib as pl

import uvicorn

from nexus.service.api import app
from nexus.service.installation import setup


def run_service(service_dir: pl.Path | None) -> None:
    """Initialize and run the Nexus service."""
    # Initialize service components
    ctx = setup.initialize_service(service_dir)

    api_app = app.create_app(ctx)

    setup.display_config(ctx.config)

    uvicorn.run(api_app, host=ctx.config.host, port=ctx.config.port, log_level=ctx.config.log_level)


def main() -> None:
    """Entry point for the Nexus service."""
    # Parse arguments
    parser = setup.create_argument_parser()
    args = parser.parse_args()

    # Check version and dependencies
    setup.handle_version_check()
    setup.verify_external_dependencies()

    # Process commands if provided
    if setup.handle_command(args):
        return

    # Determine service directory
    service_dir, first_run = setup.get_service_directory()

    # Handle first time setup
    if first_run:
        setup.prompt_installation_mode()

    # Run the service
    run_service(service_dir)


if __name__ == "__main__":
    main()
