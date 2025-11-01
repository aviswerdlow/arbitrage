"""Tests for Discord bot functionality."""

import pytest

from arbitrage.discord_bot.bot import ArbitrageBot, BotConfig


@pytest.fixture
def bot_config():
    """Create test bot configuration."""
    return BotConfig(
        token="test_token",
        channel_id=123456789,
        api_base_url="http://localhost:8000",
    )


@pytest.fixture
def bot(bot_config):
    """Create test bot instance."""
    return ArbitrageBot(bot_config)


def test_bot_initialization(bot, bot_config):
    """Bot initializes with correct config."""
    assert bot.config == bot_config
    assert len(bot._halted_venues) == 0
    assert not bot._running


def test_format_edges_empty(bot):
    """Bot formats empty edges list correctly."""
    message = bot.format_edges_message([])
    assert "No edges available" in message


def test_format_edges_with_data(bot):
    """Bot formats edges into readable message."""
    edges = [
        {
            "pair_id": "pm-0x1234:kalshi-ABC",
            "primary_market": "Test Market A",
            "hedge_market": "Test Market B",
            "net_edge_cents": 3.2,
            "expected_slippage_cents": 0.4,
            "leader": "polymarket",
        }
    ]
    message = bot.format_edges_message(edges)
    assert "Live Edges" in message
    assert "Test Market A" in message
    assert "3.2" in message


def test_format_fills_empty(bot):
    """Bot formats empty fills list correctly."""
    message = bot.format_fills_message([])
    assert "No fills yet" in message


def test_format_fills_with_data(bot):
    """Bot formats fills into readable message."""
    fills = [
        {
            "pair_id": "pm-0x1234:kalshi-ABC",
            "entry_edge_cents": 3.0,
            "realized_edge_cents": 2.5,
            "pnl_cents": 150.0,
        }
    ]
    message = bot.format_fills_message(fills)
    assert "Recent Fills" in message
    assert "1.50" in message  # PnL in dollars


def test_format_status_empty(bot):
    """Bot formats empty status correctly."""
    message = bot.format_status_message([])
    assert "No data available" in message


def test_format_status_with_data(bot):
    """Bot formats system status into readable message."""
    health = [
        {
            "venue": "polymarket",
            "feed_latency_p50_ms": 120.0,
            "feed_latency_p95_ms": 280.0,
            "error_rate": 0.001,
            "status": "healthy",
        }
    ]
    message = bot.format_status_message(health)
    assert "System Status" in message
    assert "POLYMARKET" in message
    assert "120" in message


@pytest.mark.asyncio
async def test_handle_help_command(bot):
    """Bot responds to help command."""
    response = await bot.handle_command("help", [])
    assert "Arbitrage Bot Commands" in response
    assert "/edges" in response
    assert "/halt" in response


@pytest.mark.asyncio
async def test_handle_halt_command(bot):
    """Bot halts venue trading."""
    response = await bot.handle_command("halt", ["polymarket"])
    assert "halted" in response.lower()
    assert "polymarket" in bot._halted_venues


@pytest.mark.asyncio
async def test_handle_halt_without_args(bot):
    """Bot requires venue argument for halt."""
    response = await bot.handle_command("halt", [])
    assert "Usage" in response


@pytest.mark.asyncio
async def test_handle_resume_command(bot):
    """Bot resumes venue trading."""
    # First halt
    await bot.handle_command("halt", ["kalshi"])
    assert "kalshi" in bot._halted_venues

    # Then resume
    response = await bot.handle_command("resume", ["kalshi"])
    assert "resumed" in response.lower()
    assert "kalshi" not in bot._halted_venues


@pytest.mark.asyncio
async def test_handle_resume_not_halted(bot):
    """Bot handles resume on non-halted venue."""
    response = await bot.handle_command("resume", ["polymarket"])
    assert "not halted" in response.lower()


@pytest.mark.asyncio
async def test_handle_unknown_command(bot):
    """Bot handles unknown commands gracefully."""
    response = await bot.handle_command("unknown", [])
    assert "Unknown command" in response


@pytest.mark.asyncio
async def test_alert_fill(bot):
    """Bot sends fill alert."""
    # Should not raise exception
    await bot.alert_fill(
        pair_id="test-pair",
        entry_edge_cents=3.0,
        realized_edge_cents=2.5,
        pnl_cents=150.0,
    )


@pytest.mark.asyncio
async def test_alert_error(bot):
    """Bot sends error alert."""
    await bot.alert_error("test_error", "Test error message")


@pytest.mark.asyncio
async def test_alert_threshold_breach(bot):
    """Bot sends threshold breach alert."""
    await bot.alert_threshold_breach("daily_loss", 100.0)
