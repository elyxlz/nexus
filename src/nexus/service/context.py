import dataclasses as dc

from nexus.service import config, env, logger, models


@dc.dataclass(frozen=True)
class NexusServiceContext:
    state: models.NexusServiceState
    config: config.NexusServiceConfig
    env: env.NexusServiceEnv
    logger: logger.NexusServiceLogger
