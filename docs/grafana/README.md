# RewardService Grafana 监控看板

本目录包含 rewardservice 的 Grafana 仪表盘配置文件。

## 看板列表

| 文件 | 标题 | 用途 |
|------|------|------|
| `rewardservice-overview.json` | 💰 金币经济总览 | 金币发放、优惠券兑换/使用、来源分布 |
| `rewardservice-checkin.json` | 📅 签到分析 | 签到趋势、连续天数、周奖励 |
| `rewardservice-flash-rush.json` | ⚡ 秒杀 & 整点抢 | 秒杀/整点抢趋势、售罄率 |
| `rewardservice-health.json` | 🏥 服务质量 | 接口延迟、限流、Redis 连接池 |

## 指标说明

rewardservice 通过 `/metrics` 端点暴露以下 Prometheus 指标：

### 业务指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `coins_earned_total` | Counter | 看广告赚金币总数 |
| `coin_balance_current` | Gauge | 用户金币余额（label: session_id） |
| `coupon_redeemed_total` | Counter | 优惠券兑换次数 |
| `coupon_used_total` | Counter | 优惠券实际使用次数 |
| `coupon_validate_failed_total` | Counter | 优惠券验证失败次数 |
| `cooldown_rejected_total` | Counter | 广告冷却期拒绝次数 |
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

## 使用方式

### 方式一：导入到在线 Grafana

1. 访问 https://play.grafana.org
2. 左侧 + → Import → Upload JSON file
3. 选择对应的 JSON 文件导入

### 方式二：导入到本地 Grafana

1. 启动 Prometheus + Grafana
2. 配置 Prometheus 数据源
3. 导入 JSON 文件

### 方式三：通过 Docker 一键启动

```bash
# 启动 Prometheus
docker run -d --name prometheus -p 9090:9090 \
  -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus

# 启动 Grafana
docker run -d --name grafana -p 3000:3000 grafana/grafana

# 访问 http://localhost:3000 (admin/admin)
# 添加 Prometheus 数据源后导入 JSON
```
