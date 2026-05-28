#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

required_files=(
  "scripts/deploy-monitoring.sh"
  "scripts/check-monitoring.sh"
  "scripts/run-monitoring-local-docker.sh"
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

for dashboard in docs/grafana/*.json; do
  python3 -m json.tool "$dashboard" >/dev/null
done

scripts/deploy-monitoring.sh --render >/tmp/online-boutique-monitoring-render.yaml
grep -q "name: prometheus" /tmp/online-boutique-monitoring-render.yaml
grep -q "name: grafana" /tmp/online-boutique-monitoring-render.yaml
grep -q "name: grafana-dashboards" /tmp/online-boutique-monitoring-render.yaml
grep -q "rewardservice-overview.json" /tmp/online-boutique-monitoring-render.yaml
grep -q "ad-video-stability.json" /tmp/online-boutique-monitoring-render.yaml

echo "monitoring assets verified"
