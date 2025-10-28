"""FastAPI application for the signals service."""

from datetime import datetime

from fastapi import APIRouter, FastAPI

from arbitrage.domain import HedgeIntent, MarketPair, OrderIntent, OrderSide
from arbitrage.services.base import create_app

router = APIRouter(prefix="/signals", tags=["Signals"])


@router.post("/evaluate")
async def evaluate_pair(pair: MarketPair) -> HedgeIntent:
    """Placeholder signal evaluation that returns a stub hedge intent."""

    order_intent = OrderIntent(
        venue=pair.primary_market.venue,
        market_id=pair.primary_market.id,
        side=OrderSide.BUY,
        price=0.51,
        size=10,
        max_slippage=0.004,
        created_at=datetime.utcnow(),
    )
    hedge_intent = OrderIntent(
        venue=pair.hedge_market.venue,
        market_id=pair.hedge_market.id,
        side=OrderSide.SELL,
        price=0.49,
        size=10,
        max_slippage=0.004,
        created_at=datetime.utcnow(),
    )
    return HedgeIntent(
        primary=order_intent,
        hedge=hedge_intent,
        expected_edge_cents=2.5,
        hedge_probability=0.99,
        market_pair=pair,
    )


def build_app() -> FastAPI:
    """Return configured FastAPI application."""

    app = create_app("signals")
    app.include_router(router)
    return app
