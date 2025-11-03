"""Polymarket CLOB execution client with EIP-712 signing support."""

from __future__ import annotations

import asyncio
import math
import secrets
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from eth_account import Account
from eth_account.messages import encode_structured_data
from hexbytes import HexBytes
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from arbitrage.config import get_settings
from arbitrage.domain.orders import OrderIntent, OrderSide
from arbitrage.events.models import ExecutionIntent
from arbitrage.execution.state_machine import ExecutionClient

logger = structlog.get_logger(__name__)


class PolymarketExecutionError(RuntimeError):
    """Raised when the Polymarket CLOB rejects an order or returns malformed data."""


@dataclass(slots=True)
class OrderSubmissionResult:
    """Result of a submission to the Polymarket CLOB."""

    success: bool
    order_id: str | None
    status: str
    detail: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True)
class OrderStatus:
    """Normalized view over the CLOB order status payload."""

    order_id: str
    status: str
    filled_quantity: float
    remaining_quantity: float
    average_price: float | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


def _current_epoch_seconds() -> int:
    return int(time.time())


def _price_to_ticks(price: float) -> int:
    """Represent a decimal price (0-1) as micro-dollar ticks expected by the CLOB."""

    if not (0.0 < price < 1.0):
        raise PolymarketExecutionError(
            f"Price must be between 0 and 1 for polymarket, received {price}",
        )
    return math.floor(price * 1_000_000)


def _size_to_base_units(size: float) -> int:
    """Convert contract size in USD to USDC base units (6 decimals)."""

    if size <= 0:
        raise PolymarketExecutionError(f"Size must be positive, received {size}")
    return math.floor(size * 1_000_000)


class PolymarketExecutor(ExecutionClient):
    """Execution client wrapping the Polymarket CLOB REST API."""

    ORDER_TYPES: dict[str, Any] = {
        "Order": [
            {"name": "market", "type": "address"},
            {"name": "maker", "type": "address"},
            {"name": "outcome", "type": "bytes32"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "price", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "expiry", "type": "uint256"},
            {"name": "salt", "type": "uint256"},
            {"name": "isBuy", "type": "bool"},
        ]
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        private_key: str | None = None,
        base_url: str | None = None,
        verifying_contract: str | None = None,
        chain_id: int | None = None,
        order_expiry_seconds: int | None = None,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        settings = get_settings()
        polymarket_settings = settings.polymarket

        self._api_key = api_key or settings.api_keys.polymarket_api_key
        if not self._api_key:
            raise PolymarketExecutionError(
                "Polymarket API key missing; configure POLYMARKET_API_KEY secret.",
            )

        key_to_use = private_key or settings.api_keys.polymarket_private_key
        if not key_to_use:
            raise PolymarketExecutionError(
                "Polymarket private key missing; configure POLYMARKET_PRIVATE_KEY secret.",
            )

        self._account = Account.from_key(key_to_use)
        self.address = self._account.address
        self._base_url = base_url or polymarket_settings.base_url
        self._verifying_contract = verifying_contract or polymarket_settings.verifying_contract
        self._chain_id = chain_id or polymarket_settings.chain_id
        self._expiry_seconds = order_expiry_seconds or polymarket_settings.max_order_expiry_seconds
        self._request_timeout = request_timeout

        self._domain: Mapping[str, Any] = {
            "name": "Polymarket CTF Exchange",
            "version": "1",
            "chainId": self._chain_id,
            "verifyingContract": self._verifying_contract,
        }

        self._nonce_lock = asyncio.Lock()
        self._last_nonce = int(time.time() * 1000)
        self._open_orders: MutableMapping[str, dict[str, str | None]] = {}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        self._client_provided = client is not None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._request_timeout,
        )

    async def close(self) -> None:
        """Dispose the underlying HTTP client if owned by the executor."""

        if not self._client_provided:
            await self._client.aclose()

    async def place_primary(self, intent: ExecutionIntent) -> bool:
        """Place the primary leg on Polymarket."""

        if intent.primary_order is None:
            logger.error("polymarket_primary_missing_order", intent_id=intent.intent_id)
            return False
        if intent.primary_order.venue.value != intent.edge.primary.venue:
            logger.error(
                "polymarket_primary_mismatched_venue",
                intent_id=intent.intent_id,
                expected=intent.edge.primary.venue,
                order_venue=intent.primary_order.venue.value,
            )
            return False

        result = await self._submit_order(
            intent.primary_order,
            leg="primary",
            intent_id=intent.intent_id,
        )
        if result.success and result.order_id:
            self._open_orders.setdefault(intent.intent_id, {})["primary"] = result.order_id
        return result.success

    async def hedge(self, intent: ExecutionIntent) -> bool:
        """Place the hedge leg if it also routes through Polymarket.

        Returns True when no Polymarket hedge is required to keep the state-machine moving.
        """

        order = intent.hedge_order
        if order is None:
            logger.info("polymarket_no_hedge_order", intent_id=intent.intent_id)
            return True
        if order.venue.value != intent.edge.hedge.venue:
            logger.warning(
                "polymarket_hedge_mismatched_venue",
                intent_id=intent.intent_id,
                expected=intent.edge.hedge.venue,
                order_venue=order.venue.value,
            )
            return False
        if order.venue.value != intent.edge.primary.venue:
            # This hedge should be executed on another venue (e.g., Kalshi); treat as success.
            logger.info(
                "polymarket_skip_non_polymarket_hedge",
                intent_id=intent.intent_id,
                hedge_venue=order.venue.value,
            )
            return True

        result = await self._submit_order(order, leg="hedge", intent_id=intent.intent_id)
        if result.success and result.order_id:
            self._open_orders.setdefault(intent.intent_id, {})["hedge"] = result.order_id
        return result.success

    async def cancel(self, intent: ExecutionIntent) -> None:
        """Cancel any open orders produced for this intent."""

        open_orders = self._open_orders.pop(intent.intent_id, {})
        for leg, order_id in open_orders.items():
            if order_id is None:
                continue
            try:
                await self.cancel_order(order_id)
                logger.info(
                    "polymarket_order_cancelled",
                    intent_id=intent.intent_id,
                    leg=leg,
                    order_id=order_id,
                )
            except Exception as exc:
                logger.warning(
                    "polymarket_order_cancel_failed",
                    intent_id=intent.intent_id,
                    leg=leg,
                    order_id=order_id,
                    error=str(exc),
                )

    async def cancel_order(self, order_id: str) -> bool:
        """Explicitly cancel an order."""

        url = f"/orders/{order_id}"
        response = await self._client.delete(url)
        if response.status_code not in (200, 202, 204):
            logger.warning(
                "polymarket_cancel_failed",
                order_id=order_id,
                status=response.status_code,
                body=response.text,
            )
            return False
        return True

    async def fetch_order(self, order_id: str) -> OrderStatus:
        """Retrieve order status from Polymarket."""

        url = f"/orders/{order_id}"
        response = await self._client.get(url)
        if response.status_code != 200:
            raise PolymarketExecutionError(
                f"Failed to fetch order {order_id}: {response.status_code} {response.text}",
            )
        payload = response.json()
        return self._parse_order_status(payload)

    async def _submit_order(
        self,
        order: OrderIntent,
        *,
        leg: str,
        intent_id: str,
    ) -> OrderSubmissionResult:
        """Build, sign, and submit an order."""

        expiry = _current_epoch_seconds() + self._expiry_seconds
        nonce = await self._next_nonce()

        message = self._build_order_message(order, nonce=nonce, expiry=expiry)
        typed_data = {
            "types": {"EIP712Domain": self._domain_types(), **self.ORDER_TYPES},
            "primaryType": "Order",
            "domain": dict(self._domain),
            "message": message,
        }

        signed = self._account.sign_message(encode_structured_data(typed_data))
        signature = HexBytes(signed.signature).hex()

        payload = {
            "order": {
                "market": message["market"],
                "maker": message["maker"],
                "outcome": message["outcome"],
                "makerAmount": str(message["makerAmount"]),
                "price": str(message["price"]),
                "nonce": str(message["nonce"]),
                "expiry": str(message["expiry"]),
                "salt": str(message["salt"]),
                "isBuy": message["isBuy"],
            },
            "signature": signature,
        }

        logger.info(
            "polymarket_submitting_order",
            intent_id=intent_id,
            leg=leg,
            market=order.market_id,
            side=order.side.value,
            price=order.price,
            size=order.size,
            nonce=nonce,
        )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=2.0),
            retry=retry_if_exception_type(httpx.HTTPError),
        ):
            with attempt:
                response = await self._client.post("/orders", json=payload)
                if response.status_code not in (200, 201, 202):
                    logger.warning(
                        "polymarket_order_rejected",
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
                order_id = data.get("order_id") or data.get("id")
                status = data.get("status", "accepted")
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

    def _build_order_message(
        self,
        order: OrderIntent,
        *,
        nonce: int,
        expiry: int,
    ) -> dict[str, Any]:
        """Translate OrderIntent into the EIP-712 order message."""

        market_address = self._normalize_market(order.market_id)
        maker_amount = _size_to_base_units(order.size)
        price_ticks = _price_to_ticks(order.price)

        salt = secrets.randbits(64)
        is_buy = order.side == OrderSide.BUY

        return {
            "market": market_address,
            "maker": self.address,
            "outcome": self._derive_outcome(order),
            "makerAmount": maker_amount,
            "price": price_ticks,
            "nonce": nonce,
            "expiry": expiry,
            "salt": salt,
            "isBuy": is_buy,
        }

    def _normalize_market(self, market_id: str) -> str:
        if market_id.startswith("0x"):
            return market_id
        raise PolymarketExecutionError(
            f"Polymarket market identifiers must be hex addresses, received '{market_id}'.",
        )

    def _derive_outcome(self, order: OrderIntent) -> str:
        """Derive outcome identifier; expects market_id/outcome encoding."""

        if ":" in order.market_id:
            _, outcome = order.market_id.split(":", 1)
            if outcome.startswith("0x"):
                return outcome
        # Fallback: treat market id as outcome id for binary markets.
        return order.market_id

    def _parse_order_status(self, payload: Mapping[str, Any]) -> OrderStatus:
        order_id = str(payload.get("id") or payload.get("order_id"))
        if order_id == "None":  # pragma: no cover - defensive guard when payload missing id
            raise PolymarketExecutionError("Order status payload missing identifier.")
        status = payload.get("status", "unknown")
        filled_raw = payload.get("filled_amount") or payload.get("filledAmount") or 0
        remaining_raw = payload.get("remaining_amount") or payload.get("remainingAmount") or 0
        avg_price = payload.get("average_price") or payload.get("averagePrice")
        filled = float(filled_raw) / 1_000_000
        remaining = float(remaining_raw) / 1_000_000
        if avg_price is not None:
            avg_price = float(avg_price) / 1_000_000
        return OrderStatus(
            order_id=order_id,
            status=status,
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_price=avg_price,
            raw_payload=dict(payload),
        )

    async def _next_nonce(self) -> int:
        async with self._nonce_lock:
            candidate = int(time.time() * 1000)
            self._last_nonce = max(candidate, self._last_nonce + 1)
            return self._last_nonce

    @staticmethod
    def _domain_types() -> list[dict[str, str]]:
        return [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ]


__all__ = ["PolymarketExecutor", "PolymarketExecutionError", "OrderSubmissionResult", "OrderStatus"]
