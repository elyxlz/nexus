import uvicorn

from nexus.service.api import app
from nexus.service.installation import setup


def main():
    parser = setup.create_argument_parser()
    args = parser.parse_args()

    setup.handle_version_check()
    setup.verify_external_dependencies()

    if args.command == "install":
        if args.user:
            setup.install_user(interactive=not args.no_interactive, force=args.force)
        else:
            setup.install_system(interactive=not args.no_interactive, start_service=not args.no_start, force=args.force)
        return
    elif args.command == "uninstall":
        setup.uninstall(keep_config=args.keep_config, force=args.force)
        return
    elif args.command == "config":
        setup.command_config()
        return
    elif args.command == "status":
        setup.command_status()
        return

    # If no command specified, run the service
    service_dir, first_run = setup.get_service_directory()

    # Handle first run setup
    if first_run:
        setup.prompt_installation_mode()

    # Initialize and run service
    ctx = setup.initialize_service(service_dir)
    _app = app.create_app(ctx)
    print("Running with config:")
    setup.display_config(ctx.config)
    uvicorn.run(_app, host=ctx.config.host, port=ctx.config.port, log_level=ctx.config.log_level)


if __name__ == "__main__":
    main()
