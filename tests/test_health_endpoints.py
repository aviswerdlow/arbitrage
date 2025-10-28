"""Basic tests for service health endpoints."""

import pytest
from fastapi.testclient import TestClient

from arbitrage.services.api import build_app as build_api_app
from arbitrage.services.execution import build_app as build_execution_app
from arbitrage.services.ingest import build_app as build_ingest_app
from arbitrage.services.matcher import build_app as build_matcher_app
from arbitrage.services.signals import build_app as build_signals_app


@pytest.mark.parametrize(
    "builder,service",
    [
        (build_ingest_app, "ingest"),
        (build_matcher_app, "matcher"),
        (build_signals_app, "signals"),
        (build_execution_app, "execution"),
        (build_api_app, "api"),
    ],
)
def test_health_endpoint(builder, service):
    app = builder()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == service
