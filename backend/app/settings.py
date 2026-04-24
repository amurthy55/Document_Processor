from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ESEVA_", case_sensitive=False)

    app_name: str = "eSevaCenter"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    operation_ttl_seconds: int = 2 * 60 * 60
    appdata_root: str | None = None
    configs_dir: str | None = None
    log_level: str = "info"


settings = Settings()
