"""Common helpers for FastAPI-based services."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arbitrage.config import get_settings
from arbitrage.logging import configure_logging


SERVICE_DESCRIPTION = {
    "ingest": "Normalizes venue data feeds into canonical schemas.",
    "matcher": "Matches equivalent markets across venues.",
    "signals": "Produces friction-aware arbitrage edges.",
    "execution": "Executes hedged order pairs across venues.",
    "api": "User-facing control plane and dashboards.",
}


def create_app(service_name: str) -> FastAPI:
    """Create a FastAPI app configured for the given service."""

    settings = get_settings()
    configure_logging()

    app = FastAPI(title=f"Arbitrage {service_name.title()} Service", description=SERVICE_DESCRIPTION.get(service_name, ""))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        """Basic health endpoint."""

        return {"status": "ok", "service": service_name}

    return app
