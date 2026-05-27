#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REWARD_DIR="$ROOT_DIR/src/rewardservice"
PORT="${PORT:-8091}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_ADDR="${REDIS_ADDR:-127.0.0.1:${REDIS_PORT}}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd python3
require_cmd redis-cli

if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  require_cmd redis-server
  mkdir -p /tmp/online-boutique-redis
  echo "[demo] starting redis-server on 127.0.0.1:$REDIS_PORT"
  redis-server \
    --bind 127.0.0.1 \
    --port "$REDIS_PORT" \
    --dir /tmp/online-boutique-redis \
    --daemonize yes
fi

echo "[demo] rewardservice"
echo "  health:  http://127.0.0.1:$PORT/_healthz"
echo "  demo:    http://127.0.0.1:$PORT/demo"
echo "  metrics: http://127.0.0.1:$PORT/metrics"
echo
echo "Press Ctrl-C to stop rewardservice."

cd "$REWARD_DIR"
PORT="$PORT" REDIS_ADDR="$REDIS_ADDR" python3 rewardservice.py
