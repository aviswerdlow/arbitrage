"""Tests for dashboard API endpoints."""

import pytest
from fastapi.testclient import TestClient

from arbitrage.dashboard.api import create_dashboard_app


@pytest.fixture
def client():
    """Create test client for dashboard app."""
    app = create_dashboard_app()
    return TestClient(app)


def test_index_page(client):
    """Dashboard serves HTML index page."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Arbitrage Dashboard" in response.text
    assert "Live Edges" in response.text


def test_get_edges(client):
    """API returns edges in correct format."""
    response = client.get("/api/edges")
    assert response.status_code == 200
    edges = response.json()
    assert isinstance(edges, list)
    if edges:
        edge = edges[0]
        assert "pair_id" in edge
        assert "net_edge_cents" in edge
        assert "confidence" in edge


def test_get_edges_with_limit(client):
    """API respects limit parameter."""
    response = client.get("/api/edges?limit=1")
    assert response.status_code == 200
    edges = response.json()
    assert len(edges) <= 1


def test_get_fills(client):
    """API returns fills in correct format."""
    response = client.get("/api/fills")
    assert response.status_code == 200
    fills = response.json()
    assert isinstance(fills, list)


def test_get_exposure(client):
    """API returns exposure data."""
    response = client.get("/api/exposure")
    assert response.status_code == 200
    exposure = response.json()
    assert isinstance(exposure, list)
    if exposure:
        exp = exposure[0]
        assert "venue" in exp
        assert "total_notional_usd" in exp
        assert "num_positions" in exp


def test_get_health(client):
    """API returns health metrics."""
    response = client.get("/api/health")
    assert response.status_code == 200
    health = response.json()
    assert isinstance(health, list)
    if health:
        metric = health[0]
        assert "venue" in metric
        assert "feed_latency_p50_ms" in metric
        assert "status" in metric
        assert metric["status"] in ["healthy", "degraded", "down"]
