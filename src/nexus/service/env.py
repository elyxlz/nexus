import pathlib as pl

import pydantic as pyd
import pydantic_settings as pyds


class NexusServiceEnv(pyds.BaseSettings):
    github_token: str = pyd.Field(default="")
    discord_webhook_url: str = pyd.Field(default="")
    wandb_api_key: str = pyd.Field(default="")
    wandb_entity: str = pyd.Field(default="")

    model_config = pyds.SettingsConfigDict(frozen=True, env_file_encoding="utf-8", case_sensitive=False, extra="ignore")


def save_env(env: NexusServiceEnv, env_path: pl.Path) -> None:
    env_dict = env.model_dump()
    with env_path.open("w", encoding="utf-8") as f:
        for key, value in env_dict.items():
            f.write(f"{key.upper()}={value}\n")
