"""Kalshi execution client handling JWT authentication and order placement."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from arbitrage.config import get_settings
from arbitrage.domain.orders import OrderIntent, OrderSide
from arbitrage.events.models import ExecutionIntent
from arbitrage.execution.state_machine import ExecutionClient

logger = structlog.get_logger(__name__)


class KalshiAuthError(RuntimeError):
    """Raised when authentication with Kalshi fails."""


class KalshiExecutionError(RuntimeError):
    """Raised when order placement or management fails."""


@dataclass(slots=True)
class OrderSubmissionResult:
    """Result metadata from submitting an order."""

    success: bool
    order_id: str | None
    status: str
    detail: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True)
class OrderStatus:
    """Normalized Kalshi order status payload."""

    order_id: str
    status: str
    filled_quantity: float
    remaining_quantity: float
    average_price: float | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


class KalshiExecutor(ExecutionClient):
    """Execution client for Kalshi trading API."""

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        use_demo: bool | None = None,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        settings = get_settings()
        config = settings.kalshi

        self._email = email or settings.api_keys.kalshi_email
        self._password = password or settings.api_keys.kalshi_password
        if not self._email or not self._password:
            raise KalshiAuthError(
                "Kalshi credentials missing; configure KALSHI_EMAIL and KALSHI_PASSWORD.",
            )

        use_demo_env = use_demo if use_demo is not None else config.use_demo
        base_url = config.demo_base_url if use_demo_env else config.base_url

        self._orders_path = config.orders_path
        self._order_status_path = config.order_status_path
        self._cancel_path = config.cancel_path
        self._auth_path = config.auth_path
        self._token_refresh_slack = max(config.token_refresh_slack_seconds, 1)
        self._default_time_in_force = config.default_time_in_force
        self._default_order_type = config.default_order_type

        self._client_provided = client is not None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=request_timeout,
            headers={"Content-Type": "application/json"},
        )

        self._token_lock = asyncio.Lock()
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

        self._open_orders: MutableMapping[str, dict[str, str | None]] = {}

    async def close(self) -> None:
        """Dispose the HTTP client if owned by the executor."""

        if not self._client_provided:
            await self._client.aclose()

    async def place_primary(self, intent: ExecutionIntent) -> bool:
        """Place the primary leg on Kalshi."""

        order = intent.primary_order
        if order is None:
            logger.error("kalshi_primary_missing_order", intent_id=intent.intent_id)
            return False
        if order.venue.value != intent.edge.primary.venue:
            logger.error(
                "kalshi_primary_mismatched_venue",
                intent_id=intent.intent_id,
                expected=intent.edge.primary.venue,
                order_venue=order.venue.value,
            )
            return False

        result = await self._submit_order(order, leg="primary", intent_id=intent.intent_id)
        if result.success and result.order_id:
            self._open_orders.setdefault(intent.intent_id, {})["primary"] = result.order_id
        return result.success

    async def hedge(self, intent: ExecutionIntent) -> bool:
        """Place hedge leg if routed through Kalshi."""

        order = intent.hedge_order
        if order is None:
            logger.info("kalshi_no_hedge_order", intent_id=intent.intent_id)
            return True

        if order.venue.value != intent.edge.hedge.venue:
            logger.warning(
                "kalshi_hedge_mismatched_venue",
                intent_id=intent.intent_id,
                expected=intent.edge.hedge.venue,
                order_venue=order.venue.value,
            )
            return False

        if order.venue.value != intent.edge.primary.venue:
            logger.info(
                "kalshi_skip_non_kalshi_hedge",
                intent_id=intent.intent_id,
                hedge_venue=order.venue.value,
            )
            return True

        result = await self._submit_order(order, leg="hedge", intent_id=intent.intent_id)
        if result.success and result.order_id:
            self._open_orders.setdefault(intent.intent_id, {})["hedge"] = result.order_id
        return result.success

    async def cancel(self, intent: ExecutionIntent) -> None:
        """Cancel any Kalshi orders created for this intent."""

        open_orders = self._open_orders.pop(intent.intent_id, {})
        for leg, order_id in open_orders.items():
            if order_id is None:
                continue
            try:
                cancelled = await self.cancel_order(order_id)
                logger.info(
                    "kalshi_order_cancel_attempt",
                    intent_id=intent.intent_id,
                    leg=leg,
                    order_id=order_id,
                    cancelled=cancelled,
                )
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning(
                    "kalshi_order_cancel_failed",
                    intent_id=intent.intent_id,
                    leg=leg,
                    order_id=order_id,
                    error=str(exc),
                )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""

        response = await self._authed_request("delete", self._cancel_path.format(order_id=order_id))
        if response.status_code not in (200, 202, 204):
            logger.warning(
                "kalshi_cancel_failed",
                order_id=order_id,
                status=response.status_code,
                body=response.text,
            )
            return False
        return True

    async def fetch_order(self, order_id: str) -> OrderStatus:
        """Fetch order status from Kalshi."""

        response = await self._authed_request(
            "get",
            self._order_status_path.format(order_id=order_id),
        )
        if response.status_code != 200:
            raise KalshiExecutionError(
                f"Failed to fetch order {order_id}: {response.status_code} {response.text}",
            )
        return self._parse_order_status(response.json())

    async def _submit_order(
        self,
        order: OrderIntent,
        *,
        leg: str,
        intent_id: str,
    ) -> OrderSubmissionResult:
        payload = self._build_order_payload(order)
        logger.info(
            "kalshi_submitting_order",
            intent_id=intent_id,
            leg=leg,
            market=order.market_id,
            side=order.side.value,
            price=order.price,
            size=order.size,
        )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=2.0),
            retry=retry_if_exception_type(httpx.HTTPError),
        ):
            with attempt:
                response = await self._authed_request("post", self._orders_path, json=payload)
                if response.status_code not in (200, 201, 202):
                    logger.warning(
                        "kalshi_order_rejected",
                        status=response.status_code,
                        body=response.text,
                        intent_id=intent_id,
                        leg=leg,
                    )
                    return OrderSubmissionResult(
                        success=False,
                        order_id=None,
                        status="rejected",
                        detail=response.text,
                    )

                data = response.json()
                order_id = str(data.get("order_id") or data.get("id") or "")
                status = data.get("status", "accepted")
                if not order_id:
                    logger.warning(
                        "kalshi_missing_order_id",
                        intent_id=intent_id,
                        leg=leg,
                        payload=data,
                    )
                    return OrderSubmissionResult(
                        success=False,
                        order_id=None,
                        status="unknown",
                        detail="missing order_id",
                        raw_response=data,
                    )
                return OrderSubmissionResult(
                    success=True,
                    order_id=order_id,
                    status=status,
                    raw_response=data,
                )

        return OrderSubmissionResult(
            success=False,
            order_id=None,
            status="error",
            detail="retry_exhausted",
        )

    def _build_order_payload(self, order: OrderIntent) -> dict[str, Any]:
        quantity = max(int(math.floor(order.size)), 1)
        price_cents = int(round(order.price * 100))
        price_cents = min(max(price_cents, 1), 99)

        return {
            "market_id": order.market_id,
            "side": "buy" if order.side == OrderSide.BUY else "sell",
            "type": self._default_order_type,
            "quantity": quantity,
            "price": price_cents,
            "time_in_force": self._default_time_in_force,
        }

    def _parse_order_status(self, payload: Mapping[str, Any]) -> OrderStatus:
        order_id = str(payload.get("id") or payload.get("order_id") or "")
        if not order_id:
            raise KalshiExecutionError("Order status payload missing identifier.")
        status = payload.get("status", "unknown")
        filled_raw = payload.get("filled_quantity") or payload.get("filledQuantity") or 0
        remaining_raw = payload.get("remaining_quantity") or payload.get("remainingQuantity") or 0
        avg_price_raw = payload.get("average_price") or payload.get("averagePrice")
        filled = float(filled_raw)
        remaining = float(remaining_raw)
        avg_price = float(avg_price_raw) / 100 if avg_price_raw is not None else None
        return OrderStatus(
            order_id=order_id,
            status=status,
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_price=avg_price,
            raw_payload=dict(payload),
        )

    async def _authed_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = await self._client.request(method, path, json=json, headers=headers)
        if response.status_code == 401:
            self._invalidate_token()
            await self._ensure_token(force=True)
            headers["Authorization"] = f"Bearer {self._access_token}"
            response = await self._client.request(method, path, json=json, headers=headers)
        response.raise_for_status() if response.status_code >= 500 else None
        return response

    async def _ensure_token(self, *, force: bool = False) -> None:
        async with self._token_lock:
            now = time.time()
            if (
                not force
                and self._access_token
                and now < (self._token_expiry - self._token_refresh_slack)
            ):
                return
            await self._login_locked()

    async def _login_locked(self) -> None:
        response = await self._client.post(
            self._auth_path,
            json={"email": self._email, "password": self._password},
        )
        if response.status_code != 200:
            raise KalshiAuthError(f"Kalshi login failed: {response.status_code} {response.text}")

        data = response.json()
        token = data.get("token") or data.get("access_token") or data.get("accessToken")
        if not token:
            raise KalshiAuthError("Kalshi login payload missing access token.")

        expires_at = self._extract_expiry(data)
        self._access_token = token
        self._token_expiry = expires_at
        logger.info("kalshi_authenticated", expires_at=expires_at)

    def _extract_expiry(self, payload: Mapping[str, Any]) -> float:
        expires_in = payload.get("expires_in") or payload.get("expiresIn")
        if expires_in is not None:
            try:
                return time.time() + int(expires_in)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                logger.warning("kalshi_expiry_parse_failed", source="expires_in", value=expires_in)

        expires_at = payload.get("expires_at") or payload.get("expiresAt")
        if isinstance(expires_at, (int, float)):
            return float(expires_at)
        if isinstance(expires_at, str):
            try:
                # Support ISO8601 timestamps with or without timezone suffix.
                formatted = expires_at.replace("Z", "+00:00")
                dt = datetime.fromisoformat(formatted)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.UTC)
                return dt.timestamp()
            except ValueError:  # pragma: no cover - defensive
                logger.warning("kalshi_expiry_parse_failed", source="expires_at", value=expires_at)

        # Default to 15 minutes if expiry missing.
        return time.time() + 900

    def _invalidate_token(self) -> None:
        self._access_token = None
        self._token_expiry = 0.0


__all__ = [
    "KalshiExecutor",
    "KalshiAuthError",
    "KalshiExecutionError",
    "OrderStatus",
    "OrderSubmissionResult",
]
