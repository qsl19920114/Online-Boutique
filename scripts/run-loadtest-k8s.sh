#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="default"
USERS="50"
SPAWN_RATE="5"
DURATION="15m"
HOST="frontend:80"
RESTORE="false"

usage() {
  cat <<'EOF'
Usage: scripts/run-loadtest-k8s.sh [options]

Configure and restart the Kubernetes loadgenerator deployment.

Options:
  --namespace NAME       Kubernetes namespace. Default: default
  --users N             Locust user count. Default: 50
  --spawn-rate N        Locust spawn rate. Default: 5
  --duration DURATION   Locust run time, for future-compatible images. Default: 15m
  --host HOST           Frontend host, with or without http://. Default: frontend:80
  --restore             Restore conservative defaults and restart loadgenerator
  -h, --help            Show this help

Examples:
  scripts/run-loadtest-k8s.sh --namespace default --users 100 --spawn-rate 10 --duration 20m
  scripts/run-loadtest-k8s.sh --host http://frontend.default.svc.cluster.local:80
  scripts/run-loadtest-k8s.sh --restore
EOF
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "$option requires a value" >&2
    usage >&2
    exit 1
  fi
}

strip_scheme() {
  local value="$1"
  value="${value#http://}"
  value="${value#https://}"
  value="${value%/}"
  printf '%s' "$value"
}

with_scheme() {
  local value="$1"
  value="${value%/}"
  if [[ "$value" == http://* || "$value" == https://* ]]; then
    printf '%s' "$value"
  else
    printf 'http://%s' "$value"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      require_value "$1" "${2:-}"
      NAMESPACE="$2"
      shift 2
      ;;
    --users)
      require_value "$1" "${2:-}"
      USERS="$2"
      shift 2
      ;;
    --spawn-rate)
      require_value "$1" "${2:-}"
      SPAWN_RATE="$2"
      shift 2
      ;;
    --duration)
      require_value "$1" "${2:-}"
      DURATION="$2"
      shift 2
      ;;
    --host)
      require_value "$1" "${2:-}"
      HOST="$2"
      shift 2
      ;;
    --restore)
      RESTORE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$RESTORE" == "true" ]]; then
  USERS="10"
  SPAWN_RATE="1"
  DURATION="10m"
  HOST="frontend:80"
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "missing required command: kubectl" >&2
  exit 1
fi

FRONTEND_ADDR="$(strip_scheme "$HOST")"
LOCUST_HOST="$(with_scheme "$HOST")"

echo "[loadtest] namespace: $NAMESPACE"
echo "[loadtest] users: $USERS"
echo "[loadtest] spawn rate: $SPAWN_RATE"
echo "[loadtest] duration: $DURATION"
echo "[loadtest] frontend addr: $FRONTEND_ADDR"

kubectl -n "$NAMESPACE" set env deployment/loadgenerator \
  "FRONTEND_ADDR=$FRONTEND_ADDR" \
  "USERS=$USERS" \
  "LOCUST_USERS=$USERS" \
  "LOCUST_SPAWN_RATE=$SPAWN_RATE" \
  "LOCUST_RUN_TIME=$DURATION" \
  "LOCUST_HOST=$LOCUST_HOST"

kubectl -n "$NAMESPACE" rollout restart deployment/loadgenerator
kubectl -n "$NAMESPACE" rollout status deployment/loadgenerator --timeout=180s
