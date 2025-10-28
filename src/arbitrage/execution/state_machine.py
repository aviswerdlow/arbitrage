"""Execution state machine ensuring hedged, taker-only flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol

from arbitrage.events.models import ExecutionIntent, ExecutionResult


class ExecutionState(Enum):
    READY = auto()
    PRIMARY_PLACED = auto()
    HEDGE_PLACED = auto()
    SETTLED = auto()
    FAILED = auto()


class ExecutionClient(Protocol):
    """Client responsible for placing and cancelling orders on a venue."""

    async def place_primary(self, intent: ExecutionIntent) -> bool:
        ...

    async def hedge(self, intent: ExecutionIntent) -> bool:
        ...

    async def cancel(self, intent: ExecutionIntent) -> None:
        ...


@dataclass(slots=True)
class ExecutionContext:
    """Mutable state for a single execution attempt."""

    intent: ExecutionIntent
    state: ExecutionState = ExecutionState.READY
    attempts: int = 0
    events: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionStateMachine:
    """Coordinates primary and hedge legs with strict no-legging policy."""

    client: ExecutionClient
    max_attempts: int = 2

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        """Execute the provided intent and return an audit-friendly result."""

        while ctx.attempts < self.max_attempts:
            ctx.attempts += 1
            primary = await self.client.place_primary(ctx.intent)
            if not primary:
                ctx.events.append("primary_rejected")
                continue
            ctx.state = ExecutionState.PRIMARY_PLACED

            hedge = await self.client.hedge(ctx.intent)
            if not hedge:
                ctx.events.append("hedge_failed")
                await self.client.cancel(ctx.intent)
                ctx.state = ExecutionState.FAILED
                continue

            ctx.state = ExecutionState.SETTLED
            return ExecutionResult(intent_id=ctx.intent.intent_id, success=True, message="settled")

        return ExecutionResult(
            intent_id=ctx.intent.intent_id,
            success=False,
            message=";".join(ctx.events) or "exhausted attempts",
        )


__all__ = ["ExecutionClient", "ExecutionContext", "ExecutionState", "ExecutionStateMachine"]
