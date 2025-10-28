"""FastAPI application for the ingestion service."""

from fastapi import APIRouter, FastAPI

from arbitrage.services.base import create_app

router = APIRouter(prefix="/ingest", tags=["Ingestion"])


@router.post("/venues/{venue}/markets")
async def upsert_markets(venue: str) -> dict[str, str]:
    """Placeholder endpoint for market ingestion."""

    return {"status": "accepted", "venue": venue}


def build_app() -> FastAPI:
    """Return configured FastAPI application."""

    app = create_app("ingest")
    app.include_router(router)
    return app
