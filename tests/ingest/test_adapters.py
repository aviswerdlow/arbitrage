"""Tests for venue adapter implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbitrage.ingest import KalshiAdapter, PolymarketAdapter


@pytest.fixture
def mock_polymarket_markets():
    """Sample Polymarket markets response."""
    return [
        {
            "tokenID": "0x1234",
            "ticker": "ELECTION-YES",
            "enableOrderBook": True,
            "question": "Will candidate win?",
        },
        {
            "tokenID": "0x5678",
            "ticker": "INFLATION-YES",
            "enableOrderBook": True,
            "question": "Will inflation exceed 3%?",
        },
    ]


@pytest.fixture
def mock_polymarket_orderbook():
    """Sample Polymarket orderbook response."""
    return {
        "bids": [
            {"price": "0.55", "size": "100"},
            {"price": "0.54", "size": "200"},
            {"price": "0.53", "size": "150"},
        ],
        "asks": [
            {"price": "0.56", "size": "120"},
            {"price": "0.57", "size": "180"},
            {"price": "0.58", "size": "90"},
        ],
    }


@pytest.fixture
def mock_kalshi_markets():
    """Sample Kalshi markets response."""
    return {
        "markets": [
            {
                "ticker": "KXELECTION-23NOV-YES",
                "title": "Will candidate win election?",
                "status": "open",
            },
            {
                "ticker": "KXINFLATION-23DEC-B3.0",
                "title": "Will inflation exceed 3%?",
                "status": "open",
            },
        ]
    }


@pytest.fixture
def mock_kalshi_orderbook():
    """Sample Kalshi orderbook response."""
    return {
        "orderbook": {
            "yes": [
                {"price": 55, "quantity": 100},
                {"price": 54, "quantity": 200},
            ],
            "no": [
                {"price": 45, "quantity": 120},
                {"price": 46, "quantity": 180},
            ],
        }
    }


class TestPolymarketAdapter:
    """Tests for Polymarket venue adapter."""

    @pytest.mark.asyncio
    async def test_get_markets_success(self, mock_polymarket_markets):
        """Adapter fetches and filters binary markets."""
        adapter = PolymarketAdapter()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_polymarket_markets
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            markets = await adapter.get_markets()

            assert len(markets) == 2
            assert markets[0]["tokenID"] == "0x1234"
            assert all(m.get("enableOrderBook") for m in markets)

        await adapter.close()

    @pytest.mark.asyncio
    async def test_get_orderbook_success(self, mock_polymarket_orderbook):
        """Adapter fetches orderbook for a specific token."""
        adapter = PolymarketAdapter()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_polymarket_orderbook
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            book = await adapter.get_orderbook("0x1234")

            assert len(book["bids"]) == 3
            assert len(book["asks"]) == 3
            assert float(book["bids"][0]["price"]) == 0.55

        await adapter.close()

    @pytest.mark.asyncio
    async def test_parse_orderbook_snapshot(self, mock_polymarket_orderbook):
        """Adapter correctly parses orderbook into canonical snapshot."""
        adapter = PolymarketAdapter(max_depth=3)

        snapshot = adapter._parse_orderbook_snapshot(
            token_id="0x1234",
            book_data=mock_polymarket_orderbook,
            market_symbol="TEST-YES",
        )

        assert snapshot.market.venue == "polymarket"
        assert snapshot.market.market_id == "0x1234"
        assert snapshot.market.symbol == "TEST-YES"
        assert len(snapshot.bids) == 3
        assert len(snapshot.asks) == 3
        assert snapshot.bids[0].price == 0.55
        assert snapshot.asks[0].price == 0.56

        await adapter.close()

    @pytest.mark.asyncio
    async def test_tracked_markets_filter(self):
        """Adapter filters to tracked markets when specified."""
        adapter = PolymarketAdapter(tracked_markets=["0x1234"])

        assert "0x1234" in adapter.tracked_markets
        assert "0x5678" not in adapter.tracked_markets

        await adapter.close()


class TestKalshiAdapter:
    """Tests for Kalshi venue adapter."""

    @pytest.mark.asyncio
    async def test_get_markets_success(self, mock_kalshi_markets):
        """Adapter fetches active markets."""
        adapter = KalshiAdapter()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_kalshi_markets
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            markets = await adapter.get_markets()

            assert len(markets) == 2
            assert markets[0]["ticker"] == "KXELECTION-23NOV-YES"

        await adapter.close()

    @pytest.mark.asyncio
    async def test_get_orderbook_success(self, mock_kalshi_orderbook):
        """Adapter fetches orderbook for a specific ticker."""
        adapter = KalshiAdapter()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_kalshi_orderbook
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            book = await adapter.get_orderbook("KXELECTION-23NOV-YES")

            assert len(book["yes"]) == 2
            assert len(book["no"]) == 2
            assert book["yes"][0]["price"] == 55

        await adapter.close()

    @pytest.mark.asyncio
    async def test_parse_orderbook_snapshot(self, mock_kalshi_orderbook):
        """Adapter correctly converts Kalshi YES/NO to bid/ask format."""
        adapter = KalshiAdapter(max_depth=3)

        snapshot = adapter._parse_orderbook_snapshot(
            ticker="KXELECTION-23NOV-YES",
            book_data=mock_kalshi_orderbook["orderbook"],
            market_title="Election Market",
        )

        assert snapshot.market.venue == "kalshi"
        assert snapshot.market.market_id == "KXELECTION-23NOV-YES"
        assert len(snapshot.bids) == 2
        assert len(snapshot.asks) == 2

        # YES bids become bids (prices in dollars, not cents)
        assert snapshot.bids[0].price == 0.55
        assert snapshot.bids[0].size == 100.0

        # NO bids become asks (complement pricing: 1 - NO price)
        # Sorted ascending, so 1.0 - 0.46 = 0.54 comes before 1.0 - 0.45 = 0.55
        assert snapshot.asks[0].price == 0.54  # 1.0 - 0.46
        assert snapshot.asks[1].price == 0.55  # 1.0 - 0.45

        await adapter.close()

    @pytest.mark.asyncio
    async def test_demo_mode(self):
        """Adapter uses demo URL when use_demo=True."""
        adapter = KalshiAdapter(use_demo=True)

        assert adapter.base_url == KalshiAdapter.DEMO_BASE_URL

        await adapter.close()
