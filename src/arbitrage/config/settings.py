"""Configuration models and loading utilities for the arbitrage platform."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings

from .secrets import SecretNotFoundError, SecretsManager

logger = structlog.get_logger(__name__)


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


class PolymarketTradingSettings(BaseSettings):
    """Static configuration for interacting with Polymarket's CLOB."""

    base_url: str = Field(
        "https://clob.polymarket.com",
        description="Polymarket CLOB API base URL.",
    )
    chain_id: int = Field(137, description="EVM chain id for signature domain (Polygon mainnet).")
    verifying_contract: str = Field(
        "0x3763F8612CF708662B3cBc9313d6D0E25B5fDB18",
        description="CTF Exchange contract used in the EIP-712 domain separator.",
    )
    max_order_expiry_seconds: int = Field(
        120,
        description="Default order expiry horizon applied when constructing signed payloads.",
    )


class KalshiTradingSettings(BaseSettings):
    """Configuration for Kalshi order execution endpoints and behavior."""

    base_url: str = Field(
        "https://api.elections.kalshi.com/trade-api/v2",
        description="Primary Kalshi trading API base URL.",
    )
    demo_base_url: str = Field(
        "https://demo-api.elections.kalshi.com/trade-api/v2",
        description="Demo Kalshi trading API base URL for sandbox testing.",
    )
    use_demo: bool = Field(
        False,
        description="Route API requests to the demo environment.",
    )
    auth_path: str = Field(
        "/auth/login",
        description="Relative path for JWT login endpoint.",
    )
    orders_path: str = Field(
        "/portfolio/orders",
        description="Relative path for creating orders.",
    )
    order_status_path: str = Field(
        "/portfolio/orders/{order_id}",
        description="Relative path for fetching order status details.",
    )
    cancel_path: str = Field(
        "/portfolio/orders/{order_id}",
        description="Relative path for cancelling an order.",
    )
    token_refresh_slack_seconds: int = Field(
        60,
        description="Seconds to subtract from token expiry for proactive refresh.",
    )
    default_time_in_force: str = Field(
        "IOC",
        description="Default time-in-force value for limit orders.",
    )
    default_order_type: str = Field(
        "limit",
        description="Order type to use when none provided by caller.",
    )


class ApiKeysSettings(BaseSettings):
    """API keys for external trading platforms and AI services."""

    polymarket_api_key: str | None = Field(default=None, description="Polymarket API key")
    polymarket_private_key: str | None = Field(
        default=None,
        description="Polymarket signing key for EIP-712 order submissions.",
    )
    kalshi_api_key: str | None = Field(default=None, description="Kalshi API key")
    kalshi_email: str | None = Field(
        default=None,
        description="Kalshi account email for auth flow.",
    )
    kalshi_password: str | None = Field(
        default=None,
        description="Kalshi account password for auth flow.",
    )
    deepseek_api_key: str | None = Field(default=None, description="DeepSeek API key")
    gpt4o_api_key: str | None = Field(default=None, description="GPT-4o API key")
    openai_api_key: str | None = Field(default=None, description="OpenAI platform API key.")
    discord_bot_token: str | None = Field(
        default=None,
        description="Discord bot token for alerting.",
    )


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
    polymarket: PolymarketTradingSettings
    kalshi: KalshiTradingSettings
    secrets: SecretsManager | None = field(default=None, repr=False, compare=False)
    log_level: str = "INFO"
    allowed_origins: list[str] = field(default_factory=lambda: ["*"])
    friction_pack_paths: list[Path] = field(default_factory=list)
    enabled_services: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Settings:
        """Hydrate the composed settings model from environment variables."""

        database = DatabaseSettings()
        redis = RedisSettings()
        aws = AwsSettings()
        polymarket = PolymarketTradingSettings()
        kalshi = KalshiTradingSettings()
        budgets = ServiceBudget()

        cache_ttl_raw = os.getenv("SECRETS_CACHE_TTL_SECONDS")
        cache_ttl = 300
        if cache_ttl_raw:
            try:
                cache_ttl = max(int(cache_ttl_raw), 1)
            except ValueError:
                logger.warning(
                    "invalid_secrets_cache_ttl",
                    raw_value=cache_ttl_raw,
                    default=cache_ttl,
                )

        secrets_manager = SecretsManager(
            region=aws.region,
            prefix=aws.secrets_prefix,
            cache_ttl_seconds=cache_ttl,
            enable_env_fallback=True,
        )

        api_keys = ApiKeysSettings()
        secret_field_map: dict[str, tuple[str, bool]] = {
            "POLYMARKET_API_KEY": ("polymarket_api_key", True),
            "POLYMARKET_PRIVATE_KEY": ("polymarket_private_key", True),
            "KALSHI_API_KEY": ("kalshi_api_key", False),
            "KALSHI_EMAIL": ("kalshi_email", True),
            "KALSHI_PASSWORD": ("kalshi_password", True),
            "DISCORD_BOT_TOKEN": ("discord_bot_token", False),
            "DEEPSEEK_API_KEY": ("deepseek_api_key", False),
            "OPENAI_API_KEY": ("openai_api_key", False),
        }

        secret_overrides: dict[str, str] = {}
        missing_required: list[str] = []

        for secret_name, (field_name, required) in secret_field_map.items():
            value = secrets_manager.get_secret(secret_name, default=None)
            if value is None:
                if required:
                    missing_required.append(secret_name)
                continue
            secret_overrides[field_name] = value
            if field_name == "openai_api_key":
                # Mirror OpenAI key to GPT-4o for backward compatibility.
                secret_overrides.setdefault("gpt4o_api_key", value)

        if secret_overrides:
            api_keys = api_keys.model_copy(update=secret_overrides)

        require_secrets_flag = os.getenv("REQUIRE_SECRETS", "false").lower()
        require_secrets = require_secrets_flag in {"1", "true", "yes", "on"}
        if missing_required:
            if require_secrets:
                raise SecretNotFoundError(
                    f"Missing required secrets: {', '.join(sorted(missing_required))}",
                )
            logger.warning(
                "required_secrets_missing",
                secrets=sorted(missing_required),
            )

        enabled_services_raw = os.getenv("ENABLED_SERVICES", "")
        enabled_services = [svc.strip() for svc in enabled_services_raw.split(",") if svc.strip()]

        log_level = os.getenv("LOG_LEVEL", "INFO")

        allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
        allowed_origins = [
            origin.strip()
            for origin in allowed_origins_raw.split(",")
            if origin.strip()
        ]
        if not allowed_origins:
            allowed_origins = ["*"]

        friction_paths_raw = os.getenv("FRICTION_PACK_PATHS", "")
        friction_pack_paths = [
            Path(path.strip())
            for path in friction_paths_raw.split(",")
            if path.strip()
        ]

        return cls(
            database=database,
            redis=redis,
            aws=aws,
            api_keys=api_keys,
            budgets=budgets,
            polymarket=polymarket,
            kalshi=kalshi,
            secrets=secrets_manager,
            log_level=log_level,
            allowed_origins=allowed_origins,
            friction_pack_paths=friction_pack_paths,
            enabled_services=enabled_services,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


__all__ = [
    "ApiKeysSettings",
    "AwsSettings",
    "DatabaseSettings",
    "RedisSettings",
    "ServiceBudget",
    "PolymarketTradingSettings",
    "KalshiTradingSettings",
    "Settings",
    "get_settings",
]
