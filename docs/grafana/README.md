# RewardService Grafana 监控看板

本目录包含 rewardservice 的 Grafana 仪表盘配置文件。

## 看板列表

| 文件 | 标题 | 用途 |
|------|------|------|
| `rewardservice-overview.json` | 💰 金币经济总览 | 金币发放、优惠券兑换/使用、来源分布 |
| `rewardservice-checkin.json` | 📅 签到分析 | 签到趋势、连续天数、周奖励 |
| `rewardservice-flash-rush.json` | ⚡ 秒杀 & 整点抢 | 秒杀/整点抢趋势、售罄率 |
| `rewardservice-health.json` | 🏥 服务质量 | 接口延迟、限流、Redis 连接池 |
| `ad-video-stability.json` | 广告视频播放稳定性 | 视频观看会话、播放事件、卡顿/错误、奖励领取、Chaos 注入 |
| `product-promotion-closed-loop.json` | 商品活动/优惠券闭环 | 商品活动曝光、优惠券状态流转、金币消耗/退回、RewardService p95 |

## 指标说明

rewardservice 通过 `/metrics` 端点暴露以下 Prometheus 指标：

### 业务指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `coins_earned_total` | Counter | 看广告赚金币总数 |
| `coin_balance_current` | Gauge | 最新观测到的金币余额 |
| `coupon_redeemed_total` | Counter | 优惠券兑换次数 |
| `coupon_used_total` | Counter | 优惠券实际使用次数 |
| `coupon_validate_failed_total` | Counter | 优惠券验证失败次数 |
| `coupon_lifecycle_total` | Counter | 优惠券状态流转（label: source/from_status/to_status/result） |
| `coupon_validate_total` | Counter | 优惠券校验结果（label: source/result/reason） |
| `coin_spent_total` | Counter | 金币消耗次数/数量（label: source） |
| `coin_refunded_total` | Counter | 金币退回次数/数量（label: source）；当前 checkout cancel 不退金币 |
| `cooldown_rejected_total` | Counter | 广告冷却期拒绝次数 |
| `ad_watch_session_started_total` | Counter | 视频广告观看会话创建结果（label: ad_id/creative_id/campaign_id/result） |
| `ad_watch_event_total` | Counter | 视频播放事件上报结果（label: event/ad_id/creative_id/campaign_id/result） |
| `ad_watch_progress_seconds` | Histogram | 服务端校验后的最大观看进度 |
| `ad_watch_rebuffer_total` | Counter | 视频卡顿事件数 |
| `ad_watch_error_total` | Counter | 视频播放错误数（label: error_type） |
| `ad_reward_claim_total` | Counter | 视频广告奖励领取结果（label: stage/result） |
| `ad_watch_fault_injected_total` | Counter | RewardService 广告观看链路 Chaos 注入次数（label: mode/path） |
| `checkin_total` | Counter | 签到尝试（label: result=success/duplicate） |
| `checkin_streak_histogram` | Histogram | 签到连续天数分布 |
| `weekly_bonus_total` | Counter | 周奖励发放次数 |
| `flash_sale_claimed_total` | Counter | 秒杀优惠券领取数 |
| `flash_sale_sold_out_total` | Counter | 秒杀售罄次数 |
| `rush_claimed_total` | Counter | 整点抢金币成功数 |
| `rush_sold_out_total` | Counter | 整点抢金币售罄次数 |

### 服务质量指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `reward_request_duration_seconds` | Histogram | 接口请求延迟（label: endpoint） |
| `ratelimit_rejected_total` | Counter | 限流拒绝次数（label: endpoint） |
| `redis_pool_connections_active` | Gauge | Redis 连接池活跃连接数 |

### Frontend 指标

frontend 通过 `/metrics` 暴露商品活动聚合与券代理指标：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `promotion_summary_view_total` | Counter | 商品活动汇总请求结果（label: page/has_product/result） |
| `coupon_list_proxy_total` | Counter | 优惠券列表代理请求结果（label: page/status/result） |
| `activity_event_total` | Counter | 活动前端事件（label: page/activity/action，白名单分桶） |

## 一键部署 Prometheus + Grafana

### 方式一：本机 Docker 快速验收

如果你只是想先看到 Demo 页面和 Grafana 看板联动，可以先用本机 Docker 路径：

```bash
# 终端 1：启动 rewardservice Demo
./scripts/run-reward-demo-local.sh

# 终端 2：启动本机 Prometheus + Grafana
./scripts/run-monitoring-local-docker.sh
```

访问：

```text
Demo:       http://127.0.0.1:8091/demo
Grafana:    http://127.0.0.1:3000
Prometheus: http://127.0.0.1:9090/targets
```

Grafana 默认账号：

```text
admin / admin
```

本机 Prometheus 会抓取：

```text
host.docker.internal:8091/metrics
host.docker.internal:8080/metrics
```

### 方式二：Kubernetes 部署

当前项目已经提供自包含的 Kubernetes 监控栈，不依赖 Prometheus Operator：

```bash
# 在 Online-Boutique 仓库根目录执行
./scripts/deploy-monitoring.sh
```

脚本会完成：

1. 创建 `monitoring` namespace
2. 部署 Prometheus
3. 部署 Grafana
4. 自动把本目录下的 `*.json` 看板导入 Grafana
5. 自动配置 Grafana 的 Prometheus 数据源

访问方式：

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000
```

浏览器打开：

```text
http://127.0.0.1:3000
```

默认账号：

```text
admin / admin
```

Prometheus：

```bash
kubectl -n monitoring port-forward svc/prometheus 9090:9090
```

```text
http://127.0.0.1:9090/targets
```

## 造数与前端 Demo

如果完整 Online Boutique 前端还没有跑齐，可以先直接使用 rewardservice 的独立 Demo 页面造数：

本机直接运行：

```bash
./scripts/run-reward-demo-local.sh
```

浏览器打开：

```text
http://127.0.0.1:8091/demo
```

Kubernetes 内运行时：

```bash
kubectl -n default port-forward svc/rewardservice 8088:80
```

浏览器打开：

```text
http://127.0.0.1:8088/demo
```

Demo 页面会直接调用 rewardservice 的接口：

- `POST /earn`：看广告赚金币
- `POST /checkin`：每日签到
- `POST /redeem`：金币兑换优惠券
- `POST /flash/claim`：秒杀优惠券
- `POST /rush/claim`：整点抢金币
- `GET /metrics`：查看 Prometheus 原始指标

点击 Demo 页面上的动作按钮后，等待 Prometheus 抓取周期（默认 15 秒），Grafana 看板中会出现曲线变化。

## 验证命令

检查监控栈状态：

```bash
./scripts/check-monitoring.sh
```

验证本地 manifests、脚本语法和 dashboard JSON：

```bash
./scripts/verify-monitoring-assets.sh
```

运行 Kubernetes loadgenerator 造数：

```bash
./scripts/run-loadtest-k8s.sh --namespace default --users 100 --spawn-rate 10 --duration 20m
```

恢复保守负载：

```bash
./scripts/run-loadtest-k8s.sh --namespace default --restore
```

采集 Grafana 截图：

```bash
./scripts/capture-grafana-screenshots.sh --grafana-url http://127.0.0.1:3000
```

默认输出目录：

```text
docs/screenshots/grafana/<timestamp>/
```

完整压测、监控、Chaos 与截图流程见：

```text
docs/loadtest-monitoring.md
```

只渲染监控 YAML：

```bash
./scripts/deploy-monitoring.sh --render
```

客户端 dry-run：

```bash
./scripts/deploy-monitoring.sh --dry-run=client
```

## 其他使用方式

### 在线 Grafana 手动导入

1. 访问 https://play.grafana.org
2. 左侧 + → Import → Upload JSON file
3. 选择对应的 JSON 文件导入

### 已有 Grafana 手动导入

1. 启动 Prometheus + Grafana
2. 配置 Prometheus 数据源
3. 导入 JSON 文件

### Docker 手动启动（仅适合本机已有可抓取目标）

```bash
# 需要自行准备 prometheus.yml，并把 scrape target 指向可访问的 /metrics 地址。
docker run -d --name prometheus -p 9090:9090 \
  -v /path/to/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus

# 启动 Grafana
docker run -d --name grafana -p 3000:3000 grafana/grafana

# 访问 http://localhost:3000 (admin/admin)
# 添加 Prometheus 数据源后导入 JSON
```
