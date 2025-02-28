import dataclasses as dc
import sqlite3

from nexus.server.core import config, logger

__all__ = ["NexusServerContext"]


@dc.dataclass(frozen=True)
class NexusServerContext:
    db: sqlite3.Connection
    config: config.NexusServerConfig
    logger: logger.NexusServerLogger
