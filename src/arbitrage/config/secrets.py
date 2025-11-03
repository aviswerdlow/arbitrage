"""Secrets management utilities with AWS Secrets Manager support and .env fallback."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

try:
    import boto3
except ImportError:  # pragma: no cover - boto3 is an install-time dependency
    boto3 = None

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient
else:  # pragma: no cover - typing aid only
    SecretsManagerClient = Any

logger = structlog.get_logger(__name__)


class SecretNotFoundError(RuntimeError):
    """Raised when a requested secret cannot be loaded from any source."""


@dataclass(slots=True)
class CachedSecret:
    """Cached secret payload with expiry metadata."""

    value: Any
    expires_at: float


class SecretsManager:
    """Fetch secrets from AWS Secrets Manager with local .env fallback."""

    def __init__(
        self,
        *,
        region: str | None,
        prefix: str = "",
        cache_ttl_seconds: int = 300,
        enable_env_fallback: bool = True,
    ) -> None:
        self._prefix = prefix.rstrip("/") + "/" if prefix and not prefix.endswith("/") else prefix
        self._cache_ttl = max(cache_ttl_seconds, 1)
        self._cache: dict[str, CachedSecret] = {}
        self._enable_env_fallback = enable_env_fallback
        self._logger = logger.bind(component="secrets_manager")

        self._client: SecretsManagerClient | None = None
        self._aws_enabled = bool(region and boto3 is not None)

        if enable_env_fallback:
            # Ensure environment variables from .env are available before any lookups.
            load_dotenv(override=False)

        if self._aws_enabled:
            try:
                assert boto3 is not None  # for type checkers
                self._client = boto3.client("secretsmanager", region_name=region)
            except Exception as exc:  # pragma: no cover
                # boto3 client creation can fail when AWS credentials are absent locally.
                self._logger.warning(
                    "secretsmanager_initialization_failed",
                    error=str(exc),
                    region=region,
                )
                self._aws_enabled = False
                self._client = None

    def get_secret(
        self,
        name: str,
        *,
        load_json: bool = False,
        default: Any | None = None,
        raise_on_missing: bool = False,
    ) -> Any:
        """Retrieve a secret value from AWS or environment.

        Args:
            name: Logical name of the secret without prefix. Full ARNs are also supported.
            load_json: If True, attempt to parse payloads as JSON documents.
            default: Value returned when the secret is not found and raise_on_missing is False.
            raise_on_missing: When True, raises SecretNotFoundError if the secret cannot be loaded.
        """

        secret_id = self._resolve_secret_id(name)
        cached = self._cache.get(secret_id)
        now = time.time()

        if cached and cached.expires_at > now:
            return self._maybe_deserialize(cached.value, load_json=load_json)

        loader_chain = (
            self._load_from_aws,
            self._load_from_env if self._enable_env_fallback else None,
        )

        for loader in loader_chain:
            if loader is None:
                continue
            try:
                raw_value = loader(secret_id)
            except SecretNotFoundError:
                continue
            except Exception as exc:  # pragma: no cover
                # Logging the loader failure helps diagnose credential issues.
                self._logger.warning(
                    "secret_loader_failed",
                    secret_id=secret_id,
                    loader=loader.__name__,
                    error=str(exc),
                )
                continue

            if raw_value is not None:
                self._cache[secret_id] = CachedSecret(
                    value=raw_value,
                    expires_at=now + self._cache_ttl,
                )
                return self._maybe_deserialize(raw_value, load_json=load_json)

        if raise_on_missing:
            raise SecretNotFoundError(
                f"Secret '{name}' could not be retrieved from AWS or environment.",
            )
        return default

    def clear_cache(self) -> None:
        """Invalidate any cached secret payloads."""

        self._cache.clear()

    def _load_from_aws(self, secret_id: str) -> str | None:
        if not self._aws_enabled or self._client is None:
            raise SecretNotFoundError(secret_id)

        try:
            response = self._client.get_secret_value(SecretId=secret_id)
        except (ClientError, BotoCoreError) as exc:
            self._logger.warning(
                "aws_secret_lookup_failed",
                secret_id=secret_id,
                error=str(exc),
            )
            raise SecretNotFoundError(secret_id) from exc

        secret_string = response.get("SecretString")
        if secret_string is not None:
            return secret_string

        secret_binary = response.get("SecretBinary")
        if secret_binary is not None:
            try:
                return secret_binary.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SecretNotFoundError(secret_id) from exc
        raise SecretNotFoundError(secret_id)

    def _load_from_env(self, secret_id: str) -> str | None:
        candidate_keys = self._candidate_env_keys(secret_id)
        for key in candidate_keys:
            value = os.getenv(key)
            if value is not None:
                return value
        raise SecretNotFoundError(secret_id)

    def _resolve_secret_id(self, name: str) -> str:
        if name.startswith("arn:") or name.startswith(self._prefix):
            return name
        if not self._prefix:
            return name
        return f"{self._prefix}{name}"

    def _candidate_env_keys(self, secret_id: str) -> tuple[str, ...]:
        raw_key = secret_id.split("/")[-1]
        keys = {raw_key, raw_key.upper()}
        if self._prefix and raw_key != secret_id:
            prefixed_env = secret_id.replace("/", "_").upper()
            keys.add(prefixed_env)
        return tuple(keys)

    def _maybe_deserialize(self, raw_value: str, *, load_json: bool) -> Any:
        if not load_json:
            return raw_value
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise SecretNotFoundError("Failed to decode secret payload as JSON.") from exc
