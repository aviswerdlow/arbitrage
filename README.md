# Arbitrage Platform

This repository contains the initial service scaffolding for the cross-venue binary options arbitrage system described in the PRD/TDD.

## Structure

- `src/arbitrage/config`: shared configuration management.
- `src/arbitrage/logging`: structured logging setup using `structlog`.
- `src/arbitrage/domain`: Pydantic models for markets, pairs, and order intents.
- `src/arbitrage/database`: Async SQLAlchemy engine helpers.
- `src/arbitrage/services`: FastAPI applications for ingest, matcher, signals, execution, and control-plane APIs.
- `tests`: pytest suite validating service health endpoints.

## Getting Started

Install dependencies and run the health-check tests:

```bash
pip install -e .[dev]
pytest
```

Each service exposes a `/health` endpoint; run a service locally with `uvicorn`:

```bash
uvicorn arbitrage.services.signals.app:build_app --factory
```
