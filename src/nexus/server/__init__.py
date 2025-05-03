from nexus.server.api.app import create_app
from nexus.server.installation import setup

# Expose FastAPI app directly for ASGI servers to import
ctx = setup.initialize_context(setup.get_server_directory())
app = create_app(ctx)
