#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

required_files=(
  "scripts/deploy-monitoring.sh"
  "scripts/check-monitoring.sh"
  "scripts/run-monitoring-local-docker.sh"
  "scripts/run-loadtest-k8s.sh"
  "scripts/capture-grafana-screenshots.py"
  "scripts/capture-grafana-screenshots.sh"
  "docs/loadtest-monitoring.md"
  "docs/grafana/product-promotion-closed-loop.json"
  "kubernetes-manifests/monitoring/kustomization.yaml"
  "kubernetes-manifests/monitoring/namespace.yaml"
  "kubernetes-manifests/monitoring/prometheus-rbac.yaml"
  "kubernetes-manifests/monitoring/prometheus-config.yaml"
  "kubernetes-manifests/monitoring/prometheus.yaml"
  "kubernetes-manifests/monitoring/grafana-provisioning.yaml"
  "kubernetes-manifests/monitoring/grafana.yaml"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "missing required monitoring asset: $file" >&2
    exit 1
  fi
done

bash -n scripts/deploy-monitoring.sh
bash -n scripts/check-monitoring.sh
bash -n scripts/run-reward-demo-local.sh
bash -n scripts/run-monitoring-local-docker.sh
bash -n scripts/run-loadtest-k8s.sh
bash -n scripts/capture-grafana-screenshots.sh
grep -q "FRONTEND_TARGET" scripts/run-monitoring-local-docker.sh
grep -q "frontend-local" scripts/run-monitoring-local-docker.sh
grep -q "APP_NETWORK" scripts/run-monitoring-local-docker.sh

PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/online-boutique-pycache}" \
  python3 -m py_compile scripts/capture-grafana-screenshots.py

for dashboard in docs/grafana/*.json; do
  python3 -m json.tool "$dashboard" >/dev/null
done

grep -q "coupon_lifecycle_total" docs/grafana/product-promotion-closed-loop.json
grep -q "coupon_validate_total" docs/grafana/product-promotion-closed-loop.json
grep -q "coin_spent_total" docs/grafana/product-promotion-closed-loop.json
grep -q "coin_refunded_total" docs/grafana/product-promotion-closed-loop.json
grep -q "promotion_summary_view_total" docs/grafana/product-promotion-closed-loop.json
grep -q "reward_request_duration_seconds_bucket" docs/grafana/product-promotion-closed-loop.json

grep -q "run-loadtest-k8s.sh" docs/loadtest-monitoring.md
grep -q "capture-grafana-screenshots" docs/loadtest-monitoring.md
grep -q "REWARD_WATCH_FAULT_MODE" docs/loadtest-monitoring.md
grep -q "LOCUST_SPAWN_RATE" src/loadgenerator/Dockerfile
grep -q "LOCUST_RUN_TIME" src/loadgenerator/Dockerfile

scripts/deploy-monitoring.sh --render >/tmp/online-boutique-monitoring-render.yaml
grep -q "name: prometheus" /tmp/online-boutique-monitoring-render.yaml
grep -q "name: grafana" /tmp/online-boutique-monitoring-render.yaml
grep -q "name: grafana-dashboards" /tmp/online-boutique-monitoring-render.yaml
grep -q "rewardservice-overview.json" /tmp/online-boutique-monitoring-render.yaml
grep -q "ad-video-stability.json" /tmp/online-boutique-monitoring-render.yaml
grep -q "product-promotion-closed-loop.json" /tmp/online-boutique-monitoring-render.yaml

echo "monitoring assets verified"
