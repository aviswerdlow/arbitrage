# Dashboard & Discord Bot Guide

## Overview

The arbitrage platform includes two key monitoring and control interfaces:

1. **Web Dashboard**: Real-time monitoring of edges, fills, exposure, and system health
2. **Discord Bot**: Command-based control and alerting system

Both are part of the P0 deliverables per TDD section 11 and section 17.

---

## Web Dashboard

### Features

The dashboard provides live monitoring of:

- **Live Edges**: Current arbitrage opportunities with net edge, slippage, and leader detection
- **Recent Fills**: Executed trades with entry/realized edge and PnL
- **Exposure**: Position tracking by venue and category
- **System Health**: Feed latency, error rates, and venue status

### Running the Dashboard

```bash
# Start the dashboard server
python -m arbitrage.dashboard.main

# Or using uvicorn directly
uvicorn arbitrage.dashboard.api:create_dashboard_app --factory --host 0.0.0.0 --port 8000
```

### Accessing the Dashboard

Once running, open your browser to:

```
http://localhost:8000
```

The dashboard auto-refreshes every 5 seconds to show the latest data.

### API Endpoints

The dashboard exposes REST endpoints for programmatic access:

```bash
# Get live edges
curl http://localhost:8000/api/edges?limit=10

# Get recent fills
curl http://localhost:8000/api/fills?limit=20

# Get exposure by venue
curl http://localhost:8000/api/exposure

# Get system health metrics
curl http://localhost:8000/api/health
```

### Dashboard Architecture

```
┌─────────────────────────────────────────┐
│         Browser (Auto-refresh)          │
└─────────────┬───────────────────────────┘
              │ GET /api/edges, /api/fills
              ▼
┌─────────────────────────────────────────┐
│       FastAPI Dashboard Server          │
│  - Live edges endpoint                  │
│  - Fills history endpoint               │
│  - Exposure tracking                    │
│  - Health metrics aggregation           │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│         In-Memory Storage               │
│    (Replace with DB in production)      │
└─────────────────────────────────────────┘
```

---

## Discord Bot

### Features

The Discord bot provides:

**Commands:**
- `/edges [N]` - Show top N live edges (default 5)
- `/halt <venue>` - Halt trading on polymarket or kalshi
- `/resume <venue>` - Resume trading on a venue
- `/status` - Show system health status
- `/fills [N]` - Show recent fills (default 10)
- `/help` - Show command help

**Alerts:**
- Fill execution notifications with PnL
- Error condition alerts
- Risk threshold breach warnings

### Setup

1. **Install Discord bot dependencies:**

```bash
pip install -e .[bot]
```

2. **Create a Discord bot:**

   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application
   - Add a bot to your application
   - Copy the bot token

3. **Configure bot:**

```python
from arbitrage.discord_bot import ArbitrageBot, BotConfig

config = BotConfig(
    token="YOUR_DISCORD_BOT_TOKEN",
    channel_id=123456789,  # Your Discord channel ID
    api_base_url="http://localhost:8000"  # Dashboard API URL
)

bot = ArbitrageBot(config)
```

### Running the Bot

```python
import asyncio
from arbitrage.discord_bot import ArbitrageBot, BotConfig

async def main():
    config = BotConfig(
        token="YOUR_BOT_TOKEN",
        channel_id=YOUR_CHANNEL_ID,
        api_base_url="http://localhost:8000"
    )

    bot = ArbitrageBot(config)

    # In production, use discord.py to connect to Discord
    # For now, the bot provides the command handling logic
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### Command Examples

```
/edges 5
📊 Live Edges

1. US Election - Trump Yes ↔️ PRES-TRUMP-YES
   Edge: 3.20¢ | Slippage: 0.40¢ | Leader: polymarket

2. CPI >= 3.0% Dec 2024 ↔️ KXINFLATION-24DEC-B3.0
   Edge: 2.80¢ | Slippage: 0.30¢ | Leader: kalshi
```

```
/halt polymarket
🛑 Trading halted on POLYMARKET
```

```
/status
🔧 System Status

🟢 POLYMARKET
   Latency: p50=120ms p95=280ms
   Error Rate: 0.10%

🟢 KALSHI
   Latency: p50=150ms p95=320ms
   Error Rate: 0.20%
```

### Alert Examples

The bot automatically sends alerts for important events:

```
✅ Fill Executed: pm-0x1234:kalshi-ABC...
Entry Edge: 3.20¢ → Realized: 2.50¢ → PnL: $1.50
```

```
🚨 Error (execution_timeout): Hedge failed to complete within 250ms
```

```
🚨 Threshold Breach: daily_loss = $-50.00
```

### Bot Architecture

```
┌─────────────────────────────────────────┐
│          Discord Server                 │
│     User sends: /edges 5                │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│       ArbitrageBot (Command Router)     │
│  - Parse commands                       │
│  - Format responses                     │
│  - Send alerts                          │
└─────────────┬───────────────────────────┘
              │ HTTP API calls
              ▼
┌─────────────────────────────────────────┐
│       Dashboard API Server              │
│  /api/edges, /api/fills, etc           │
└─────────────────────────────────────────┘
```

---

## Integration with Main System

### Sending Data to Dashboard

From your trading engine, post edges and fills to the dashboard:

```python
import httpx

# Post a new edge
async with httpx.AsyncClient() as client:
    edge = {
        "pair_id": "pm-0x1234:kalshi-ABC",
        "primary_market": "Market A",
        "hedge_market": "Market B",
        "net_edge_cents": 3.2,
        "confidence": 0.88,
        "expected_slippage_cents": 0.4,
        "leader": "polymarket",
        "timestamp": "2024-01-01T00:00:00Z"
    }
    await client.post("http://localhost:8000/api/edges", json=edge)

# Post a fill
async with httpx.AsyncClient() as client:
    fill = {
        "fill_id": "fill-001",
        "timestamp": "2024-01-01T00:00:00Z",
        "pair_id": "pm-0x1234:kalshi-ABC",
        "entry_edge_cents": 3.2,
        "realized_edge_cents": 2.5,
        "slippage_cents": 0.4,
        "fees_cents": 0.3,
        "size_usd": 100.0,
        "pnl_cents": 150.0
    }
    await client.post("http://localhost:8000/api/fills", json=fill)
```

### Triggering Bot Alerts

From your trading engine, trigger bot alerts:

```python
from arbitrage.discord_bot import ArbitrageBot, BotConfig

bot = ArbitrageBot(config)

# Alert on fill
await bot.alert_fill(
    pair_id="pm-0x1234:kalshi-ABC",
    entry_edge_cents=3.2,
    realized_edge_cents=2.5,
    pnl_cents=150.0
)

# Alert on error
await bot.alert_error("execution_timeout", "Hedge timed out")

# Alert on threshold breach
await bot.alert_threshold_breach("daily_loss", 50.0)
```

---

## Production Deployment

### Dashboard (Docker)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install -e .

EXPOSE 8000

CMD ["uvicorn", "arbitrage.dashboard.api:create_dashboard_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

### Discord Bot (Docker)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install -e .[bot]

CMD ["python", "run_discord_bot.py"]
```

### Environment Variables

```bash
# Dashboard
DASHBOARD_PORT=8000

# Discord Bot
DISCORD_BOT_TOKEN=your_token_here
DISCORD_CHANNEL_ID=123456789
DASHBOARD_API_URL=http://dashboard:8000
```

---

## Monitoring Checklist (per TDD §11)

Dashboard provides:
- ✅ Live edges: pair, net edge, best side, lead-lag leader, confidence
- ✅ Recent fills: entry edge vs realized, slippage, fees
- ✅ Exposure: by venue, by category
- ✅ Health: feed latency p50/p95, error rates

Discord bot provides:
- ✅ `/edges top N` command
- ✅ `/halt venue` command
- ✅ `/resume venue` command
- ✅ Fill alerts
- ✅ Error alerts
- ✅ Threshold breach alerts

---

## Next Steps

1. **Connect to Real Data**: Replace in-memory storage with database queries
2. **Add Authentication**: Secure dashboard with login system
3. **Deploy to Production**: Use Docker and AWS ECS per TDD §12
4. **Enable Discord Integration**: Connect bot to Discord servers using discord.py
5. **Add More Metrics**: P&L curves, Sharpe tracking, drawdown visualization

For more details on the overall system architecture, see the [TDD](./TDD.md) and [PRD](./PRD.md).
