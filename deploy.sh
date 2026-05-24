#!/bin/bash
# Online Boutique 一键部署脚本
# 支持：本地 minikube / kind / GKE
set -e

MODE=${1:-"local"}   # local | gke
NAMESPACE=${2:-"default"}

echo "====== Online Boutique 部署 ======"
echo "模式: $MODE | 命名空间: $NAMESPACE"

check_deps() {
  for cmd in kubectl docker; do
    command -v $cmd &>/dev/null || { echo "缺少依赖: $cmd"; exit 1; }
  done
}

deploy_local() {
  echo "[1/3] 检查 minikube..."
  if command -v minikube &>/dev/null; then
    minikube status &>/dev/null || minikube start --memory=4096 --cpus=4
    eval $(minikube docker-env)
    echo "[2/3] 使用预构建镜像部署（gcr.io）..."
  elif command -v kind &>/dev/null; then
    echo "[2/3] 使用 kind 集群..."
  fi

  echo "[3/3] 应用 Kubernetes manifests..."
  kubectl apply -f ./release/kubernetes-manifests.yaml -n $NAMESPACE

  echo ""
  echo "等待 Pod 就绪..."
  kubectl wait --for=condition=ready pod --all -n $NAMESPACE --timeout=300s 2>/dev/null || true
  kubectl get pods -n $NAMESPACE

  echo ""
  echo "访问方式："
  if command -v minikube &>/dev/null; then
    echo "  minikube service frontend-external -n $NAMESPACE"
  else
    echo "  kubectl port-forward svc/frontend-external 8080:80 -n $NAMESPACE"
    echo "  然后访问 http://localhost:8080"
  fi
}

deploy_gke() {
  if [ -z "$PROJECT_ID" ]; then
    echo "请设置 PROJECT_ID 环境变量"; exit 1
  fi
  REGION=${REGION:-"us-central1"}

  echo "[1/4] 启用 GKE API..."
  gcloud services enable container.googleapis.com --project=${PROJECT_ID}

  echo "[2/4] 创建 GKE 集群..."
  gcloud container clusters create-auto online-boutique \
    --project=${PROJECT_ID} --region=${REGION} 2>/dev/null || \
    echo "集群已存在，跳过创建"

  echo "[3/4] 获取集群凭证..."
  gcloud container clusters get-credentials online-boutique \
    --project=${PROJECT_ID} --region=${REGION}

  echo "[4/4] 部署应用..."
  kubectl apply -f ./release/kubernetes-manifests.yaml -n $NAMESPACE

  echo ""
  echo "等待 Pod 就绪（约 3-5 分钟）..."
  kubectl get pods -n $NAMESPACE -w &
  WATCH_PID=$!
  sleep 180
  kill $WATCH_PID 2>/dev/null || true

  EXTERNAL_IP=""
  for i in {1..20}; do
    EXTERNAL_IP=$(kubectl get service frontend-external -n $NAMESPACE \
      --output jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
    [ -n "$EXTERNAL_IP" ] && break
    echo "等待外部 IP... ($i/20)"
    sleep 15
  done

  echo ""
  echo "====== 部署完成 ======"
  echo "访问地址: http://${EXTERNAL_IP}"
}

deploy_monitoring() {
  echo "[监控] 部署 Prometheus + Grafana..."
  kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/main/bundle.yaml 2>/dev/null || true

  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: $NAMESPACE
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: 'kubernetes-pods'
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\d+)?;(\d+)
            replacement: \$1:\$2
            target_label: __address__
EOF
  echo "Prometheus 配置完成"
}

check_deps

case $MODE in
  local)   deploy_local ;;
  gke)     deploy_gke ;;
  monitor) deploy_monitoring ;;
  all-gke) deploy_gke; deploy_monitoring ;;
  *)
    echo "用法: ./deploy.sh [local|gke|monitor|all-gke] [namespace]"
    echo "  local   - 本地 minikube/kind 部署（使用预构建镜像）"
    echo "  gke     - Google GKE 部署（需要 PROJECT_ID 环境变量）"
    echo "  monitor - 仅部署监控组件"
    echo "  all-gke - GKE + 监控一起部署"
    exit 1
    ;;
esac
