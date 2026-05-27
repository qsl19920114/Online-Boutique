#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONITORING_NAMESPACE="monitoring"
ACTION="apply"
WAIT_FOR_ROLLOUT="true"
PORT_FORWARD="false"

usage() {
  cat <<'EOF'
Usage: scripts/deploy-monitoring.sh [options]

Deploy a self-contained Prometheus + Grafana stack for Online Boutique.

Options:
  --namespace NAME             Monitoring namespace. Default: monitoring
  --render                     Render Kubernetes YAML to stdout
  --dry-run=client             Run kubectl client-side dry-run
  --dry-run=server             Run kubectl server-side dry-run
  --skip-wait                  Do not wait for rollout
  --port-forward               After deploy, open Grafana/Prometheus port-forwards
  -h, --help                   Show this help

Examples:
  scripts/deploy-monitoring.sh
  scripts/deploy-monitoring.sh --render
  scripts/deploy-monitoring.sh --dry-run=client
  scripts/deploy-monitoring.sh --port-forward
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      MONITORING_NAMESPACE="$2"
      shift 2
      ;;
    --render)
      ACTION="render"
      shift
      ;;
    --dry-run=client)
      ACTION="dry-run-client"
      shift
      ;;
    --dry-run=server)
      ACTION="dry-run-server"
      shift
      ;;
    --skip-wait)
      WAIT_FOR_ROLLOUT="false"
      shift
      ;;
    --port-forward)
      PORT_FORWARD="true"
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

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

dashboard_args() {
  local found="false"
  for dashboard in "$ROOT_DIR"/docs/grafana/*.json; do
    if [[ -f "$dashboard" ]]; then
      found="true"
      printf '%s\0' "--from-file=$dashboard"
    fi
  done
  if [[ "$found" != "true" ]]; then
    echo "no Grafana dashboard JSON files found in docs/grafana" >&2
    exit 1
  fi
}

render_dashboards_configmap() {
  local args=()
  while IFS= read -r -d '' item; do
    args+=("$item")
  done < <(dashboard_args)

  kubectl create configmap grafana-dashboards \
    --namespace "$MONITORING_NAMESPACE" \
    "${args[@]}" \
    --dry-run=client \
    -o yaml
}

render_static_manifests() {
  kubectl kustomize "$ROOT_DIR/kubernetes-manifests/monitoring"
}

render_all() {
  render_static_manifests
  echo "---"
  render_dashboards_configmap
}

apply_all() {
  echo "[monitoring] applying namespace"
  kubectl apply -f "$ROOT_DIR/kubernetes-manifests/monitoring/namespace.yaml"

  echo "[monitoring] applying Grafana dashboards from docs/grafana"
  render_dashboards_configmap | kubectl apply -f -

  echo "[monitoring] applying Prometheus + Grafana manifests"
  kubectl apply -k "$ROOT_DIR/kubernetes-manifests/monitoring"

  if [[ "$WAIT_FOR_ROLLOUT" == "true" ]]; then
    echo "[monitoring] waiting for Prometheus"
    kubectl -n "$MONITORING_NAMESPACE" rollout status deploy/prometheus --timeout=240s
    echo "[monitoring] waiting for Grafana"
    kubectl -n "$MONITORING_NAMESPACE" rollout status deploy/grafana --timeout=240s
  fi

  cat <<EOF

Monitoring stack is deployed.

Grafana:
  kubectl -n $MONITORING_NAMESPACE port-forward svc/grafana 3000:3000
  http://127.0.0.1:3000
  login: admin / admin

Prometheus:
  kubectl -n $MONITORING_NAMESPACE port-forward svc/prometheus 9090:9090
  http://127.0.0.1:9090

RewardService demo page, after the app is running:
  kubectl -n default port-forward svc/rewardservice 8088:80
  http://127.0.0.1:8088/demo
EOF

  if [[ "$PORT_FORWARD" == "true" ]]; then
    echo
    echo "[monitoring] opening port-forwards. Press Ctrl-C to stop."
    kubectl -n "$MONITORING_NAMESPACE" port-forward svc/prometheus 9090:9090 >/tmp/online-boutique-prometheus-port-forward.log 2>&1 &
    local prometheus_pid=$!
    trap 'kill "$prometheus_pid" 2>/dev/null || true' EXIT
    kubectl -n "$MONITORING_NAMESPACE" port-forward svc/grafana 3000:3000
  fi
}

require_cmd kubectl

case "$ACTION" in
  render)
    render_all
    ;;
  dry-run-client)
    render_all | kubectl apply --dry-run=client -f -
    ;;
  dry-run-server)
    render_all | kubectl apply --server-side --dry-run=server -f -
    ;;
  apply)
    apply_all
    ;;
  *)
    echo "unsupported action: $ACTION" >&2
    exit 1
    ;;
esac
