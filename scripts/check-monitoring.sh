#!/usr/bin/env bash
set -euo pipefail

MONITORING_NAMESPACE="monitoring"
APP_NAMESPACE="default"

usage() {
  cat <<'EOF'
Usage: scripts/check-monitoring.sh [options]

Check the Online Boutique monitoring stack and key scrape targets.

Options:
  --namespace NAME       Monitoring namespace. Default: monitoring
  --app-namespace NAME   Application namespace. Default: default
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      MONITORING_NAMESPACE="$2"
      shift 2
      ;;
    --app-namespace)
      APP_NAMESPACE="$2"
      shift 2
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

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd kubectl

echo "[check] monitoring deployments"
kubectl -n "$MONITORING_NAMESPACE" rollout status deploy/prometheus --timeout=120s
kubectl -n "$MONITORING_NAMESPACE" rollout status deploy/grafana --timeout=120s

echo
echo "[check] monitoring services"
kubectl -n "$MONITORING_NAMESPACE" get svc prometheus grafana

echo
echo "[check] application pods with metrics annotations"
kubectl -n "$APP_NAMESPACE" get pods \
  -o custom-columns=NAME:.metadata.name,APP:.metadata.labels.app,SCRAPE:.metadata.annotations.prometheus\\.io/scrape,PORT:.metadata.annotations.prometheus\\.io/port,PATH:.metadata.annotations.prometheus\\.io/path

echo
echo "[check] quick Prometheus query"
if command -v curl >/dev/null 2>&1; then
  kubectl -n "$MONITORING_NAMESPACE" port-forward svc/prometheus 19090:9090 >/tmp/online-boutique-prometheus-check.log 2>&1 &
  pf_pid=$!
  trap 'kill "$pf_pid" 2>/dev/null || true' EXIT
  sleep 2
  curl -fsS "http://127.0.0.1:19090/api/v1/query?query=up" | grep -E '"status":"success"|coins_earned_total|rewardservice|frontend|checkoutservice' || {
    echo "Prometheus responded, but expected target names were not visible yet."
  }
  kill "$pf_pid" 2>/dev/null || true
  trap - EXIT
else
  echo "curl not found; skipping Prometheus HTTP check"
fi

echo
echo "monitoring check completed"
