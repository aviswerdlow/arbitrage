"""FastAPI application for the matcher service."""

from fastapi import APIRouter, FastAPI

from arbitrage.domain import MarketPair
from arbitrage.services.base import create_app

router = APIRouter(prefix="/matcher", tags=["Matcher"])


@router.post("/pairs")
async def register_pair(pair: MarketPair) -> dict[str, str]:
    """Placeholder endpoint to register a market pair."""

    return {"status": "accepted", "pair_id": pair.id}


def build_app() -> FastAPI:
    """Return configured FastAPI application."""

    app = create_app("matcher")
    app.include_router(router)
    return app
