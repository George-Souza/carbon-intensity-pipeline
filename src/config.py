from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    api_base_url: str = "https://api.carbonintensity.org.uk"

    http_connect_timeout: float = 10.0
    http_read_timeout: float = 30.0
    http_max_attempts: int = 4
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    backfill_pause_seconds: float = 0.5

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
