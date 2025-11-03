# Repository Guidelines

This document captures the working agreements for the Arbitrage platform so agents can contribute without second-guessing expectations.

## Project Structure & Module Organization
Source lives under `src/arbitrage/` with domain-specific subpackages (`ingest`, `matching`, `signals`, `dashboard`, `discord_bot`, etc.). Shared Pydantic models sit in `src/arbitrage/domain`, while configuration defaults and environment parsing live in `src/arbitrage/config`. Integration and unit tests mirror this layout inside `tests/`, and reference docs, PRD, and TDD artifacts reside in `docs/`.

## Build, Test, and Development Commands
Install dependencies with `pip install -e .[dev]`. Run the full test suite via `pytest`, add `-k pattern` for focused runs, and use `pytest --cov=arbitrage --cov-report=html` when validating coverage before review. Start the dashboard locally with `python -m arbitrage.dashboard.main`; confirm the UI at `http://localhost:8000`. To exercise the Discord bot, layer on `pip install -e .[bot]` and follow `docs/DASHBOARD_BOT_GUIDE.md`.

## Coding Style & Naming Conventions
Code targets Python 3.11 with Ruff configured for linting (`ruff check .`) and formatting (`ruff format .`) using 4-space indentation, LF endings, and 100-character lines. Prefer descriptive module and class names aligned with venue, signal, or risk semantics (e.g., `KalshiOrderBook`, `LeadLagAnalyzer`). Type hints are required on public APIs; run `mypy` locally before opening a PR. Keep logging structured with `structlog` and avoid `print`.

## Testing Guidelines
Tests are written with pytest and pytest-asyncio; place synchronous helpers in `tests/conftest.py` and async fixtures under relevant modules. Name files `test_*.py` and functions `test_<behavior>` to match discovery. Backtests and signal calculations should include boundary cases for fees, slippage, and latency envelopes. Aim to maintain or improve coverage when touching risk, execution, or matching logic.

## Commit & Pull Request Guidelines
Commits in this repo use concise, sentence-case summaries (e.g., “Add friction model coverage checks”). Favor logical, reviewable units and reference tickets or docs inline when applicable. Pull requests must describe motivation, functional impact, and verification (commands run, new tests). Link related docs in `docs/` and include screenshots for dashboard/UI changes. Request a second reviewer when modifying execution, risk, or matching code paths.

## Security & Configuration Tips
Sensitive credentials are loaded via environment variables consumed by `src/arbitrage/config/settings.py`; never commit secrets or `.env` files. Verify rate limits and API keys in staging before enabling live trading adapters. When introducing external integrations, document failure modes and fallback behavior in `docs/`.
