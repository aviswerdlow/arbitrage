"""Configuration models and loading utilities for the arbitrage platform."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class DatabaseSettings(BaseSettings):
    """Runtime configuration for Postgres connectivity."""

    dsn: str = Field(
        ...,
        description="SQLAlchemy-compatible DSN for the primary Postgres instance.",
    )
    schema: str = Field(
        "public",
        description="Default schema used for arbitrage platform tables.",
    )


class RedisSettings(BaseSettings):
    """Redis connectivity for the event bus and caching layers."""

    url: str = Field(..., description="Redis connection URL including credentials.")
    queue_prefix: str = Field(
        "arb",
        description="Prefix prepended to Redis streams and queues for namespacing.",
    )


class AwsSettings(BaseSettings):
    """AWS-specific configuration for secrets and telemetry sinks."""

    region: str = Field(..., description="AWS region containing shared resources.")
    secrets_prefix: str = Field(
        "arbitrage/",
        description="Base path for secrets stored in AWS Secrets Manager.",
    )


class ApiKeysSettings(BaseSettings):
    """API keys for external trading platforms and AI services."""

    polymarket_api_key: Optional[str] = Field(default=None, description="Polymarket API key")
    kalshi_api_key: Optional[str] = Field(default=None, description="Kalshi API key")
    deepseek_api_key: Optional[str] = Field(default=None, description="DeepSeek API key")
    gpt4o_api_key: Optional[str] = Field(default=None, description="GPT-4o API key")


class ServiceBudget(BaseSettings):
    """Latency budgets and retry policies enforced across services."""

    alert_to_order_ms: int = Field(100, description="Maximum alert-to-order latency.")
    hedge_completion_ms: int = Field(250, description="p95 hedge completion budget.")
    max_retries: int = Field(2, description="Maximum retries allowed per request.")


@dataclass(slots=True)
class Settings:
    """Aggregated application settings loaded from environment variables."""

    database: DatabaseSettings
    redis: RedisSettings
    aws: AwsSettings
    api_keys: ApiKeysSettings
    budgets: ServiceBudget
    friction_pack_paths: List[Path] = field(default_factory=list)
    enabled_services: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        """Hydrate the composed settings model from environment variables."""

        database = DatabaseSettings()
        redis = RedisSettings()
        aws = AwsSettings()
        api_keys = ApiKeysSettings()
        budgets = ServiceBudget()

        enabled_services_raw = os.getenv("ENABLED_SERVICES", "")
        enabled_services = [svc.strip() for svc in enabled_services_raw.split(",") if svc.strip()]

        friction_paths_raw = os.getenv("FRICTION_PACK_PATHS", "")
        friction_pack_paths = [Path(path.strip()) for path in friction_paths_raw.split(",") if path.strip()]

        return cls(
            database=database,
            redis=redis,
            aws=aws,
            api_keys=api_keys,
            budgets=budgets,
            friction_pack_paths=friction_pack_paths,
            enabled_services=enabled_services,
        )


__all__ = [
    "ApiKeysSettings",
    "AwsSettings",
    "DatabaseSettings",
    "RedisSettings",
    "ServiceBudget",
    "Settings",
]
