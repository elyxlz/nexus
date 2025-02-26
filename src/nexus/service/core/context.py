import dataclasses as dc
import sqlite3

from nexus.service.core import config, env, logger

__all__ = ["NexusServiceContext"]


@dc.dataclass(frozen=True)
class NexusServiceContext:
    db: sqlite3.Connection
    config: config.NexusServiceConfig
    env: env.NexusServiceEnv
    logger: logger.NexusServiceLogger
