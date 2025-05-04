from pyrqlite import dbapi2 as dbapi
from nexus.server.core.config import NexusServerConfig

def connect(cfg: NexusServerConfig):
    host, port = cfg.rqlite_host.split(":")
    return dbapi.connect(
        host=host,
        port=int(port),
        user="nexus",
        password=cfg.api_key,
        https=False,
        verify_https=False,
    )