"""Application configuration definitions."""

from functools import lru_cache
from typing import List

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    """Centralised application configuration."""

    environment: str = Field(default="development", description="Runtime environment")
    database_url: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/arbitrage",
        description="SQLAlchemy connection string",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    log_level: str = Field(default="INFO", description="Logging level for all services")
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"], description="CORS origins")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()
