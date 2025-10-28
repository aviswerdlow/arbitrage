"""FastAPI application for the control-plane API."""

from fastapi import APIRouter, FastAPI

from arbitrage.domain import HedgeIntent, MarketPair
from arbitrage.services.base import create_app

router = APIRouter(prefix="/api", tags=["Control"])


@router.get("/pairs")
async def list_pairs() -> list[MarketPair]:
    """Return an empty list for now."""

    return []


@router.post("/intents")
async def submit_intent(intent: HedgeIntent) -> dict[str, str]:
    """Accept an intent for external review."""

    return {"status": "queued", "primary": intent.primary.market_id}


def build_app() -> FastAPI:
    """Return configured FastAPI application."""

    app = create_app("api")
    app.include_router(router)
    return app
