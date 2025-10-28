"""FastAPI application for the execution service."""

from datetime import datetime

from fastapi import APIRouter, FastAPI

from arbitrage.domain import HedgeIntent
from arbitrage.services.base import create_app

router = APIRouter(prefix="/execution", tags=["Execution"])


@router.post("/orders")
async def execute_intent(intent: HedgeIntent) -> dict[str, str]:
    """Placeholder execution endpoint that echoes an execution id."""

    execution_id = f"exec-{int(datetime.utcnow().timestamp())}"
    return {"status": "submitted", "execution_id": execution_id, "primary_market": intent.primary.market_id}


def build_app() -> FastAPI:
    """Return configured FastAPI application."""

    app = create_app("execution")
    app.include_router(router)
    return app
