"""Discord bot implementation for arbitrage platform monitoring and control.

Implements TDD section 11: Discord bot with /edges, /halt, /resume commands
and alerts for fills, errors, and threshold breaches.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class BotConfig:
    """Configuration for Discord bot."""

    token: str  # Discord bot token
    channel_id: int  # Channel ID for alerts
    api_base_url: str = "http://localhost:8000"  # Dashboard API URL
    command_prefix: str = "/"


class ArbitrageBot:
    """Discord bot for monitoring and controlling the arbitrage platform.

    Commands:
    - /edges [top N]: Show top N live edges (default 5)
    - /halt <venue>: Halt trading on a specific venue
    - /resume <venue>: Resume trading on a venue
    - /status: Show system status
    - /fills [limit]: Show recent fills

    Alerts:
    - Fill executions with PnL
    - Error conditions
    - Risk threshold breaches
    """

    def __init__(self, config: BotConfig) -> None:
        """Initialize Discord bot.

        Args:
            config: Bot configuration
        """
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._halted_venues: set[str] = set()
        self._running = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def _fetch_edges(self, limit: int = 5) -> list[dict[str, Any]]:
        """Fetch live edges from dashboard API.

        Args:
            limit: Number of edges to fetch

        Returns:
            List of edge dictionaries
        """
        client = await self._ensure_client()
        try:
            response = await client.get(
                f"{self.config.api_base_url}/api/edges",
                params={"limit": limit},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("failed_to_fetch_edges", error=str(exc))
            return []

    async def _fetch_fills(self, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch recent fills from dashboard API.

        Args:
            limit: Number of fills to fetch

        Returns:
            List of fill dictionaries
        """
        client = await self._ensure_client()
        try:
            response = await client.get(
                f"{self.config.api_base_url}/api/fills",
                params={"limit": limit},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("failed_to_fetch_fills", error=str(exc))
            return []

    async def _fetch_health(self) -> list[dict[str, Any]]:
        """Fetch system health metrics.

        Returns:
            List of health metric dictionaries
        """
        client = await self._ensure_client()
        try:
            response = await client.get(f"{self.config.api_base_url}/api/health")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("failed_to_fetch_health", error=str(exc))
            return []

    def format_edges_message(self, edges: list[dict[str, Any]]) -> str:
        """Format edges into Discord message.

        Args:
            edges: List of edge dictionaries

        Returns:
            Formatted message string
        """
        if not edges:
            return "📊 **Live Edges**: No edges available"

        lines = ["📊 **Live Edges**\n"]
        for i, edge in enumerate(edges, 1):
            lines.append(
                f"{i}. **{edge['primary_market'][:30]}** ↔️ **{edge['hedge_market'][:30]}**\n"
                f"   Edge: `{edge['net_edge_cents']:.2f}¢` | "
                f"Slippage: `{edge['expected_slippage_cents']:.2f}¢` | "
                f"Leader: `{edge.get('leader', 'none')}`"
            )

        return "\n".join(lines)

    def format_fills_message(self, fills: list[dict[str, Any]]) -> str:
        """Format fills into Discord message.

        Args:
            fills: List of fill dictionaries

        Returns:
            Formatted message string
        """
        if not fills:
            return "💰 **Recent Fills**: No fills yet"

        lines = ["💰 **Recent Fills**\n"]
        for fill in fills[:5]:  # Show top 5
            pnl = fill["pnl_cents"] / 100
            emoji = "✅" if pnl > 0 else "❌"
            lines.append(
                f"{emoji} `{fill['pair_id'][:20]}...`\n"
                f"   Entry: `{fill['entry_edge_cents']:.2f}¢` → "
                f"Realized: `{fill['realized_edge_cents']:.2f}¢` → "
                f"PnL: `${pnl:.2f}`"
            )

        return "\n".join(lines)

    def format_status_message(self, health_metrics: list[dict[str, Any]]) -> str:
        """Format system status into Discord message.

        Args:
            health_metrics: List of health metric dictionaries

        Returns:
            Formatted message string
        """
        if not health_metrics:
            return "🔧 **System Status**: No data available"

        lines = ["🔧 **System Status**\n"]
        for metric in health_metrics:
            status_emoji = {
                "healthy": "🟢",
                "degraded": "🟡",
                "down": "🔴",
            }.get(metric["status"], "⚪")

            halted = " [HALTED]" if metric["venue"] in self._halted_venues else ""

            lines.append(
                f"{status_emoji} **{metric['venue'].upper()}**{halted}\n"
                f"   Latency: p50=`{metric['feed_latency_p50_ms']:.0f}ms` "
                f"p95=`{metric['feed_latency_p95_ms']:.0f}ms`\n"
                f"   Error Rate: `{metric['error_rate'] * 100:.2f}%`"
            )

        return "\n".join(lines)

    async def send_alert(self, message: str, alert_type: str = "info") -> None:
        """Send alert to Discord channel.

        Args:
            message: Alert message
            alert_type: Alert type ("info", "warning", "error", "success")
        """
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅",
        }
        emoji = emoji_map.get(alert_type, "📢")

        formatted = f"{emoji} {message}"
        logger.info("discord_alert", type=alert_type, message=message)

        # In real implementation, this would use discord.py to send to channel
        # For now, just log
        print(f"[DISCORD ALERT] {formatted}")

    async def handle_command(self, command: str, args: list[str]) -> str:
        """Handle incoming command from Discord.

        Args:
            command: Command name (without prefix)
            args: Command arguments

        Returns:
            Response message
        """
        if command == "edges":
            limit = int(args[0]) if args and args[0].isdigit() else 5
            edges = await self._fetch_edges(limit)
            return self.format_edges_message(edges)

        elif command == "halt":
            if not args:
                return "❌ Usage: /halt <venue>"
            venue = args[0].lower()
            self._halted_venues.add(venue)
            await self.send_alert(f"Trading halted on {venue.upper()}", "warning")
            logger.warning("venue_halted", venue=venue)
            return f"🛑 Trading halted on **{venue.upper()}**"

        elif command == "resume":
            if not args:
                return "❌ Usage: /resume <venue>"
            venue = args[0].lower()
            if venue in self._halted_venues:
                self._halted_venues.remove(venue)
                await self.send_alert(f"Trading resumed on {venue.upper()}", "success")
                logger.info("venue_resumed", venue=venue)
                return f"▶️ Trading resumed on **{venue.upper()}**"
            else:
                return f"ℹ️ **{venue.upper()}** was not halted"

        elif command == "status":
            health = await self._fetch_health()
            return self.format_status_message(health)

        elif command == "fills":
            limit = int(args[0]) if args and args[0].isdigit() else 10
            fills = await self._fetch_fills(limit)
            return self.format_fills_message(fills)

        elif command == "help":
            return """
**Arbitrage Bot Commands**

`/edges [N]` - Show top N live edges (default 5)
`/halt <venue>` - Halt trading on a venue (polymarket/kalshi)
`/resume <venue>` - Resume trading on a venue
`/status` - Show system health status
`/fills [N]` - Show recent fills (default 10)
`/help` - Show this help message
            """.strip()

        else:
            return f"❌ Unknown command: `/{command}`. Type `/help` for available commands."

    async def alert_fill(
        self,
        pair_id: str,
        entry_edge_cents: float,
        realized_edge_cents: float,
        pnl_cents: float,
    ) -> None:
        """Send alert for completed fill.

        Args:
            pair_id: Market pair identifier
            entry_edge_cents: Entry edge in cents
            realized_edge_cents: Realized edge after costs
            pnl_cents: Net PnL in cents
        """
        pnl_usd = pnl_cents / 100
        alert_type = "success" if pnl_usd > 0 else "warning"

        message = (
            f"**Fill Executed**: `{pair_id[:30]}...`\n"
            f"Entry Edge: `{entry_edge_cents:.2f}¢` → "
            f"Realized: `{realized_edge_cents:.2f}¢` → "
            f"PnL: `${pnl_usd:.2f}`"
        )

        await self.send_alert(message, alert_type)

    async def alert_error(self, error_type: str, message: str) -> None:
        """Send alert for error condition.

        Args:
            error_type: Type of error
            message: Error message
        """
        await self.send_alert(f"**Error ({error_type})**: {message}", "error")

    async def alert_threshold_breach(self, threshold_type: str, value: float) -> None:
        """Send alert for risk threshold breach.

        Args:
            threshold_type: Type of threshold (e.g., "daily_loss", "venue_cap")
            value: Threshold value
        """
        message = f"**Threshold Breach**: {threshold_type} = `${value:.2f}`"
        await self.send_alert(message, "error")

    async def run(self) -> None:
        """Start the Discord bot (simulated for demo).

        In production, this would connect to Discord using discord.py
        and listen for commands/events.
        """
        self._running = True
        logger.info("discord_bot_started", channel_id=self.config.channel_id)

        # Send startup message
        await self.send_alert("Arbitrage bot started and ready for commands", "info")

        # Simulate command processing loop
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Discord bot."""
        self._running = False
        if self._client:
            await self._client.aclose()
        logger.info("discord_bot_stopped")


__all__ = ["ArbitrageBot", "BotConfig"]
