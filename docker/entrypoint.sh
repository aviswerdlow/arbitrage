#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE_NAME:-api}"
PORT="${PORT:-8000}"
LOG_LEVEL="${LOG_LEVEL:-info}"

case "${SERVICE}" in
  ui)
    APP_PATH="arbitrage.dashboard.api:create_dashboard_app"
    ;;
  ingest|matcher|signals|execution|api)
    APP_PATH="arbitrage.services.${SERVICE}.app:build_app"
    ;;
  *)
    echo "Unsupported SERVICE_NAME '${SERVICE}'. Expected one of: ingest, matcher, signals, execution, api, ui." >&2
    exit 1
    ;;
esac

exec uvicorn "${APP_PATH}" --factory --host 0.0.0.0 --port "${PORT}" --log-level "${LOG_LEVEL}"

