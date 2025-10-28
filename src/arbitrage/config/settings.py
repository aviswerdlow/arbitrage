"""Application configuration definitions."""

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised application configuration."""

    environment: str = Field(default="development", description="Runtime environment")
    api_host: str = Field(default="0.0.0.0", description="Host for API services")
    api_port: int = Field(default=8000, description="Port for API services")
    database_url: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/arbitrage",
        description="SQLAlchemy connection string",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    log_level: str = Field(default="INFO", description="Logging level for all services")
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"], description="CORS origins")

    polymarket_api_key: Optional[str] = Field(default=None, description="Polymarket API key")
    kalshi_api_key: Optional[str] = Field(default=None, description="Kalshi API key")
    deepseek_api_key: Optional[str] = Field(default=None, description="DeepSeek API key")
    gpt4o_api_key: Optional[str] = Field(default=None, description="GPT-4o API key")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()
