import dataclasses as dc
from pyrqlite.connections import Connection as RqliteConnection

from nexus.server.core import config

__all__ = ["NexusServerContext"]


@dc.dataclass(frozen=True, slots=True)
class NexusServerContext:
    db: RqliteConnection
    config: config.NexusServerConfig
