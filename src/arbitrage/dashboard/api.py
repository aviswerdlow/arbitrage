"""FastAPI dashboard application with REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from structlog import get_logger

logger = get_logger(__name__)


class EdgeResponse(BaseModel):
    """Live edge opportunity."""

    pair_id: str
    primary_market: str
    hedge_market: str
    net_edge_cents: float
    confidence: float
    expected_slippage_cents: float
    leader: str | None
    timestamp: datetime


class FillResponse(BaseModel):
    """Executed fill record."""

    fill_id: str
    timestamp: datetime
    pair_id: str
    entry_edge_cents: float
    realized_edge_cents: float
    slippage_cents: float
    fees_cents: float
    size_usd: float
    pnl_cents: float


class ExposureResponse(BaseModel):
    """Current exposure by venue."""

    venue: str
    total_notional_usd: float
    num_positions: int
    category_breakdown: dict[str, float]


class HealthResponse(BaseModel):
    """System health metrics."""

    venue: str
    feed_latency_p50_ms: float
    feed_latency_p95_ms: float
    error_rate: float
    last_update: datetime
    status: str  # "healthy", "degraded", "down"


# In-memory storage for demo (replace with database queries in production)
_edges: list[EdgeResponse] = []
_fills: list[FillResponse] = []
_exposures: dict[str, ExposureResponse] = {}
_health_metrics: dict[str, HealthResponse] = {}


def create_dashboard_app() -> FastAPI:
    """Create and configure dashboard FastAPI application."""
    app = FastAPI(title="Arbitrage Dashboard", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve main dashboard page."""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>Arbitrage Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: #00d9ff; margin-bottom: 10px; font-size: 24px; }
        h2 { color: #00d9ff; margin: 20px 0 10px; font-size: 18px; border-bottom: 1px solid #1a1f3a; padding-bottom: 8px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .card {
            background: #151933;
            border: 1px solid #1a1f3a;
            border-radius: 8px;
            padding: 15px;
        }
        .stat { display: flex; justify-content: space-between; margin-bottom: 8px; }
        .stat-label { color: #8b92b8; font-size: 13px; }
        .stat-value { color: #00d9ff; font-weight: 600; font-size: 14px; }
        .positive { color: #00ff88; }
        .negative { color: #ff4757; }
        table { width: 100%; border-collapse: collapse; background: #151933; border-radius: 8px; overflow: hidden; }
        th { background: #1a1f3a; padding: 12px; text-align: left; color: #8b92b8; font-weight: 600; font-size: 12px; text-transform: uppercase; }
        td { padding: 12px; border-top: 1px solid #1a1f3a; font-size: 13px; }
        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .status-healthy { background: #00ff88; }
        .status-degraded { background: #ffa502; }
        .status-down { background: #ff4757; }
        .refresh {
            background: #00d9ff;
            color: #0a0e27;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            margin-bottom: 20px;
        }
        .refresh:hover { background: #00b8d4; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 Arbitrage Platform Dashboard</h1>
        <p style="color: #8b92b8; margin-bottom: 20px; font-size: 13px;">
            Real-time monitoring of cross-venue arbitrage opportunities
        </p>

        <button class="refresh" onclick="loadData()">🔄 Refresh Data</button>

        <div class="grid">
            <div class="card">
                <h2>System Status</h2>
                <div id="system-status">Loading...</div>
            </div>
            <div class="card">
                <h2>Quick Stats</h2>
                <div id="quick-stats">Loading...</div>
            </div>
        </div>

        <h2>📊 Live Edges</h2>
        <div style="overflow-x: auto; margin-bottom: 20px;">
            <table id="edges-table">
                <thead>
                    <tr>
                        <th>Pair</th>
                        <th>Primary</th>
                        <th>Hedge</th>
                        <th>Net Edge</th>
                        <th>Slippage</th>
                        <th>Leader</th>
                        <th>Confidence</th>
                        <th>Time</th>
                    </tr>
                </thead>
                <tbody id="edges-body">
                    <tr><td colspan="8" style="text-align: center; color: #8b92b8;">Loading edges...</td></tr>
                </tbody>
            </table>
        </div>

        <h2>💰 Recent Fills</h2>
        <div style="overflow-x: auto; margin-bottom: 20px;">
            <table id="fills-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Pair</th>
                        <th>Entry Edge</th>
                        <th>Realized Edge</th>
                        <th>Slippage</th>
                        <th>Fees</th>
                        <th>Size</th>
                        <th>PnL</th>
                    </tr>
                </thead>
                <tbody id="fills-body">
                    <tr><td colspan="8" style="text-align: center; color: #8b92b8;">No fills yet</td></tr>
                </tbody>
            </table>
        </div>

        <h2>📈 Exposure by Venue</h2>
        <div class="grid" id="exposure-grid">
            <div class="card"><p style="color: #8b92b8;">Loading exposure data...</p></div>
        </div>

        <h2>🔧 Health Metrics</h2>
        <div class="grid" id="health-grid">
            <div class="card"><p style="color: #8b92b8;">Loading health metrics...</p></div>
        </div>
    </div>

    <script>
        async function loadData() {
            try {
                // Load edges
                const edgesRes = await fetch('/api/edges');
                const edges = await edgesRes.json();
                renderEdges(edges);

                // Load fills
                const fillsRes = await fetch('/api/fills');
                const fills = await fillsRes.json();
                renderFills(fills);

                // Load exposure
                const exposureRes = await fetch('/api/exposure');
                const exposure = await exposureRes.json();
                renderExposure(exposure);

                // Load health
                const healthRes = await fetch('/api/health');
                const health = await healthRes.json();
                renderHealth(health);

                // Update stats
                updateStats(edges, fills);

            } catch (err) {
                console.error('Failed to load data:', err);
            }
        }

        function renderEdges(edges) {
            const tbody = document.getElementById('edges-body');
            if (edges.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #8b92b8;">No live edges</td></tr>';
                return;
            }
            tbody.innerHTML = edges.map(e => `
                <tr>
                    <td>${e.pair_id.slice(0, 12)}...</td>
                    <td>${e.primary_market.slice(0, 20)}</td>
                    <td>${e.hedge_market.slice(0, 20)}</td>
                    <td class="positive">${e.net_edge_cents.toFixed(2)}¢</td>
                    <td>${e.expected_slippage_cents.toFixed(2)}¢</td>
                    <td>${e.leader || '-'}</td>
                    <td>${(e.confidence * 100).toFixed(0)}%</td>
                    <td>${new Date(e.timestamp).toLocaleTimeString()}</td>
                </tr>
            `).join('');
        }

        function renderFills(fills) {
            const tbody = document.getElementById('fills-body');
            if (fills.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #8b92b8;">No fills yet</td></tr>';
                return;
            }
            tbody.innerHTML = fills.slice(0, 10).map(f => `
                <tr>
                    <td>${new Date(f.timestamp).toLocaleTimeString()}</td>
                    <td>${f.pair_id.slice(0, 12)}...</td>
                    <td>${f.entry_edge_cents.toFixed(2)}¢</td>
                    <td class="${f.realized_edge_cents > 0 ? 'positive' : 'negative'}">
                        ${f.realized_edge_cents.toFixed(2)}¢
                    </td>
                    <td>${f.slippage_cents.toFixed(2)}¢</td>
                    <td>${f.fees_cents.toFixed(2)}¢</td>
                    <td>$${f.size_usd.toFixed(2)}</td>
                    <td class="${f.pnl_cents > 0 ? 'positive' : 'negative'}">
                        ${f.pnl_cents > 0 ? '+' : ''}${(f.pnl_cents / 100).toFixed(2)}
                    </td>
                </tr>
            `).join('');
        }

        function renderExposure(exposures) {
            const grid = document.getElementById('exposure-grid');
            if (exposures.length === 0) {
                grid.innerHTML = '<div class="card"><p style="color: #8b92b8;">No exposure data</p></div>';
                return;
            }
            grid.innerHTML = exposures.map(exp => `
                <div class="card">
                    <h3 style="color: #00d9ff; margin-bottom: 10px; text-transform: capitalize;">${exp.venue}</h3>
                    <div class="stat">
                        <span class="stat-label">Total Notional</span>
                        <span class="stat-value">$${exp.total_notional_usd.toFixed(2)}</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Positions</span>
                        <span class="stat-value">${exp.num_positions}</span>
                    </div>
                </div>
            `).join('');
        }

        function renderHealth(healthMetrics) {
            const grid = document.getElementById('health-grid');
            if (healthMetrics.length === 0) {
                grid.innerHTML = '<div class="card"><p style="color: #8b92b8;">No health data</p></div>';
                return;
            }
            grid.innerHTML = healthMetrics.map(h => `
                <div class="card">
                    <h3 style="color: #00d9ff; margin-bottom: 10px; text-transform: capitalize;">
                        <span class="status-dot status-${h.status}"></span>${h.venue}
                    </h3>
                    <div class="stat">
                        <span class="stat-label">Feed Latency (p50)</span>
                        <span class="stat-value">${h.feed_latency_p50_ms.toFixed(0)}ms</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Feed Latency (p95)</span>
                        <span class="stat-value">${h.feed_latency_p95_ms.toFixed(0)}ms</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Error Rate</span>
                        <span class="stat-value">${(h.error_rate * 100).toFixed(2)}%</span>
                    </div>
                    <div class="stat">
                        <span class="stat-label">Last Update</span>
                        <span class="stat-value">${new Date(h.last_update).toLocaleTimeString()}</span>
                    </div>
                </div>
            `).join('');
        }

        function updateStats(edges, fills) {
            const statsDiv = document.getElementById('quick-stats');
            const totalPnl = fills.reduce((sum, f) => sum + f.pnl_cents, 0) / 100;
            const avgEdge = edges.length > 0
                ? edges.reduce((sum, e) => sum + e.net_edge_cents, 0) / edges.length
                : 0;

            statsDiv.innerHTML = `
                <div class="stat">
                    <span class="stat-label">Live Edges</span>
                    <span class="stat-value">${edges.length}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Total Fills</span>
                    <span class="stat-value">${fills.length}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Total PnL</span>
                    <span class="stat-value ${totalPnl >= 0 ? 'positive' : 'negative'}">
                        ${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}
                    </span>
                </div>
                <div class="stat">
                    <span class="stat-label">Avg Edge</span>
                    <span class="stat-value">${avgEdge.toFixed(2)}¢</span>
                </div>
            `;

            const statusDiv = document.getElementById('system-status');
            statusDiv.innerHTML = `
                <div class="stat">
                    <span class="stat-label">Status</span>
                    <span class="stat-value">
                        <span class="status-dot status-healthy"></span>Operational
                    </span>
                </div>
                <div class="stat">
                    <span class="stat-label">Uptime</span>
                    <span class="stat-value">99.8%</span>
                </div>
            `;
        }

        // Auto-refresh every 5 seconds
        setInterval(loadData, 5000);

        // Initial load
        loadData();
    </script>
</body>
</html>
        """

    @app.get("/api/edges", response_model=list[EdgeResponse])
    async def get_edges(limit: int = 20) -> list[EdgeResponse]:
        """Get current live edges sorted by net edge."""
        # Demo data - replace with actual database queries
        if not _edges:
            # Generate some demo edges
            now = datetime.now(UTC)
            _edges.extend([
                EdgeResponse(
                    pair_id="pm-0x1234:kalshi-ABC123",
                    primary_market="US Election - Trump Yes",
                    hedge_market="PRES-TRUMP-YES",
                    net_edge_cents=3.2,
                    confidence=0.88,
                    expected_slippage_cents=0.4,
                    leader="polymarket",
                    timestamp=now,
                ),
                EdgeResponse(
                    pair_id="pm-0x5678:kalshi-DEF456",
                    primary_market="CPI >= 3.0% Dec 2024",
                    hedge_market="KXINFLATION-24DEC-B3.0",
                    net_edge_cents=2.8,
                    confidence=0.92,
                    expected_slippage_cents=0.3,
                    leader="kalshi",
                    timestamp=now - timedelta(seconds=5),
                ),
            ])

        return sorted(_edges, key=lambda x: x.net_edge_cents, reverse=True)[:limit]

    @app.get("/api/fills", response_model=list[FillResponse])
    async def get_fills(limit: int = 50) -> list[FillResponse]:
        """Get recent fill history."""
        return sorted(_fills, key=lambda x: x.timestamp, reverse=True)[:limit]

    @app.get("/api/exposure", response_model=list[ExposureResponse])
    async def get_exposure() -> list[ExposureResponse]:
        """Get current exposure by venue."""
        if not _exposures:
            _exposures["polymarket"] = ExposureResponse(
                venue="polymarket",
                total_notional_usd=1250.0,
                num_positions=5,
                category_breakdown={"politics": 750.0, "economics": 500.0},
            )
            _exposures["kalshi"] = ExposureResponse(
                venue="kalshi",
                total_notional_usd=980.0,
                num_positions=4,
                category_breakdown={"economics": 600.0, "weather": 380.0},
            )
        return list(_exposures.values())

    @app.get("/api/health", response_model=list[HealthResponse])
    async def get_health() -> list[HealthResponse]:
        """Get system health metrics by venue."""
        if not _health_metrics:
            now = datetime.now(UTC)
            _health_metrics["polymarket"] = HealthResponse(
                venue="polymarket",
                feed_latency_p50_ms=120.0,
                feed_latency_p95_ms=280.0,
                error_rate=0.001,
                last_update=now,
                status="healthy",
            )
            _health_metrics["kalshi"] = HealthResponse(
                venue="kalshi",
                feed_latency_p50_ms=150.0,
                feed_latency_p95_ms=320.0,
                error_rate=0.002,
                last_update=now,
                status="healthy",
            )
        return list(_health_metrics.values())

    @app.post("/api/edges")
    async def add_edge(edge: EdgeResponse) -> dict[str, str]:
        """Add a new edge to the live feed (for testing)."""
        _edges.append(edge)
        return {"status": "success", "edge_id": edge.pair_id}

    @app.post("/api/fills")
    async def add_fill(fill: FillResponse) -> dict[str, str]:
        """Record a new fill (for testing)."""
        _fills.append(fill)
        return {"status": "success", "fill_id": fill.fill_id}

    return app


__all__ = ["create_dashboard_app"]
