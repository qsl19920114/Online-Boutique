#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$ROOT_DIR/.monitoring-local"
REWARD_TARGET="${REWARD_TARGET:-host.docker.internal:8091}"
PROMETHEUS_IMAGE="${PROMETHEUS_IMAGE:-prom/prometheus:v2.55.1}"
GRAFANA_IMAGE="${GRAFANA_IMAGE:-grafana/grafana:11.3.1}"
PROMETHEUS_CONTAINER="${PROMETHEUS_CONTAINER:-online-boutique-prometheus}"
GRAFANA_CONTAINER="${GRAFANA_CONTAINER:-online-boutique-grafana}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd docker

mkdir -p "$WORK_DIR/datasources" "$WORK_DIR/dashboards"

cat >"$WORK_DIR/prometheus.yml" <<EOF
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: rewardservice-local
    metrics_path: /metrics
    static_configs:
      - targets:
          - "$REWARD_TARGET"
        labels:
          app: rewardservice
          namespace: local
EOF

cat >"$WORK_DIR/datasources/prometheus.yaml" <<'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://host.docker.internal:9090
    isDefault: true
    editable: true
EOF

cat >"$WORK_DIR/dashboards/dashboards.yaml" <<'EOF'
apiVersion: 1
providers:
  - name: rewardservice
    orgId: 1
    folder: RewardService
    type: file
    disableDeletion: false
    editable: true
    updateIntervalSeconds: 5
    options:
      path: /var/lib/grafana/dashboards/rewardservice
EOF

docker rm -f "$PROMETHEUS_CONTAINER" "$GRAFANA_CONTAINER" >/dev/null 2>&1 || true

docker run -d \
  --name "$PROMETHEUS_CONTAINER" \
  --add-host=host.docker.internal:host-gateway \
  -p 9090:9090 \
  -v "$WORK_DIR/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  "$PROMETHEUS_IMAGE" >/dev/null

docker run -d \
  --name "$GRAFANA_CONTAINER" \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -e GF_SECURITY_ADMIN_USER=admin \
  -e GF_SECURITY_ADMIN_PASSWORD=admin \
  -e GF_USERS_ALLOW_SIGN_UP=false \
  -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
  -e GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/rewardservice/rewardservice-overview.json \
  -v "$WORK_DIR/datasources:/etc/grafana/provisioning/datasources:ro" \
  -v "$WORK_DIR/dashboards:/etc/grafana/provisioning/dashboards:ro" \
  -v "$ROOT_DIR/docs/grafana:/var/lib/grafana/dashboards/rewardservice:ro" \
  "$GRAFANA_IMAGE" >/dev/null

cat <<EOF
Local monitoring is running.

Prometheus:
  http://127.0.0.1:9090/targets

Grafana:
  http://127.0.0.1:3000
  login: admin / admin

RewardService target:
  http://$REWARD_TARGET/metrics

Stop:
  docker rm -f $PROMETHEUS_CONTAINER $GRAFANA_CONTAINER
EOF
