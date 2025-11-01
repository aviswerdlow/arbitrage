"""Main entry point for running the dashboard server."""

import uvicorn

from arbitrage.dashboard.api import create_dashboard_app


def main() -> None:
    """Run the dashboard server."""
    app = create_dashboard_app()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
