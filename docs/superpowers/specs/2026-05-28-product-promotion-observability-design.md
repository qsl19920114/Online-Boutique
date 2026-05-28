# Product Promotion Observability Design

## 背景

当前分支已经完成视频广告观看会话、播放事件上报、`/earn` 服务端校验、广告稳定性指标和广告压测链路。下一阶段要把能力从“广告链路单点增强”推进到“以商品为中心的活动闭环 + 可观测压测闭环”。

现状问题：

- 商品页只零散展示补贴、广告和奖励入口，不能直接判断某个商品有哪些活动。
- Rewards 页能兑换券，但缺少“我的券”视图，用户兑换、领取、使用、取消的闭环不完整。
- 结算失败取消券时，当前 Lua 逻辑会把 `locked -> pending` 同时退金币，形成“券可继续使用且金币已退”的账务漏洞。
- 监控已有奖励、秒杀、整点抢、广告视频稳定性看板，但缺少商品活动归因和优惠券生命周期视角。
- 压测已有 Locust 用户流和广告观看流，但缺少统一脚本、Grafana 截图工具和故障注入后的观测说明。

## 目标

1. 在商品页新增“活动与优惠”模块，按商品聚合展示百亿补贴、秒杀券、整点抢金币、看广告赚金币、用户已有券和金币可兑换券。
2. 新增 frontend 聚合接口 `GET /promotion/summary?product_id=...`，把原本散落的 rewardservice 查询聚合到一个稳定前端 API。
3. 修复 coupon cancel 账务语义：结算取消只恢复券状态，不退金币；后续如需退券换金币，单独新增 refund API。
4. Rewards 页面新增“我的券”展示，并展示当前可参与活动。
5. 购物车展示用户可用券，形成商品页发现活动、Rewards 获取/兑换券、购物车使用券、结算提交/取消的闭环。
6. 增加低基数活动归因指标和优惠券生命周期指标，避免直接使用 `product_id`、`coupon_code`、`session_id` 作为 Prometheus label。
7. 新增商品活动/优惠券闭环 Grafana 看板。
8. 新增自动化压测脚本、Grafana 截图脚本和使用文档，说明压测、看板、Chaos 注入后的核心指标变化。

## 非目标

- 不新建独立广告播放服务。
- 不引入数据库，活动配置继续保留在当前服务内存/Redis 结构中。
- 不把 `product_id`、`coupon_code`、`session_id` 直接作为指标 label。
- 不在 checkout cancel 中做金币退款；退款能力后续作为独立业务动作设计。
- 不要求本地仓库自带真实广告 mp4，视频广告继续使用可配置 URL 和 fallback。

## 接口设计

### Frontend 聚合接口

`GET /promotion/summary?product_id=<id>`

响应：

```json
{
  "product_id": "OLJCESPC7Z",
  "coin_balance": 42,
  "subsidy": {
    "active": true,
    "discount_pct": 20,
    "label": "亿补价"
  },
  "flash": {
    "active": true,
    "discount_pct": 70,
    "cost_coins": 0,
    "remaining": 12,
    "source": "flash"
  },
  "rush": {
    "active": true,
    "coins": 10,
    "remaining": 60
  },
  "owned_coupons": [
    {
      "code": "CPNABC123",
      "discount_pct": 90,
      "status": "pending",
      "source": "redeem",
      "cost_coins": 50
    }
  ],
  "redeem_options": [
    {
      "cost_coins": 50,
      "discount_pct": 90,
      "affordable": false
    },
    {
      "cost_coins": 100,
      "discount_pct": 80,
      "affordable": false
    }
  ],
  "ad_rewards": [
    {
      "stage": 1,
      "trigger_sec": 10,
      "coins": 5
    }
  ]
}
```

说明：

- `product_id` 仅存在于响应体，不作为 Prometheus label。
- `owned_coupons` 默认只返回 `pending` 和 `locked`；前端可用于商品页/购物车展示。
- `redeem_options` 从当前 rewardservice 规则镜像构建，当前固定为 50 金币 9 折、100 金币 8 折。
- `ad_rewards` 复用已上线的视频广告阶段奖励语义。

### Frontend 券代理接口

`GET /coupons?status=pending`

说明：

- 代理 rewardservice `GET /coupons?session_id=...&status=...`。
- 用于 Rewards 页和购物车页显示用户券。
- 返回结构保持 rewardservice 原始响应，降低重复转换成本。

## Coupon 生命周期语义

状态流：

```text
pending --validate--> locked --commit--> used
pending --validate--> locked --cancel--> pending
```

取消语义：

- `coupon_cancel` 只处理结算失败/用户放弃支付后的锁券回滚。
- 当券是 `locked` 时，回到 `pending`，不退金币。
- 当券不是 `locked` 时，返回对应业务错误。
- 已兑换券的金币支出发生在 `redeem` 或 `flash_claim`，不会因为 checkout cancel 被撤销。

后续如果需要退券换金币，新增独立接口：

```text
POST /coupon/refund
```

并要求券状态、有效期、退款次数、来源、审计日志单独设计。

## 指标设计

新增 rewardservice 指标：

| 指标 | 类型 | Label | 说明 |
|------|------|-------|------|
| `coupon_lifecycle_total` | Counter | `source`,`from_status`,`to_status`,`result` | 券状态流转结果 |
| `coupon_validate_total` | Counter | `source`,`result`,`reason` | 券校验结果 |
| `coin_spent_total` | Counter | `source` | 金币支出 |
| `coin_refunded_total` | Counter | `source` | 金币退款；本阶段 cancel 不再增加 |

新增 frontend 指标：

| 指标 | 类型 | Label | 说明 |
|------|------|-------|------|
| `promotion_summary_view_total` | Counter | `page`,`has_product`,`result` | 聚合活动摘要请求结果 |
| `coupon_list_proxy_total` | Counter | `page`,`status`,`result` | 前端券列表代理请求结果 |
| `activity_event_total` | Counter | `page`,`activity`,`action` | 前端埋点事件，白名单 label |

Label 白名单：

- `page`: `home`、`product`、`rewards`、`cart`、`ad`、`other`
- `activity`: `subsidy`、`flash`、`rush`、`ad_video`、`coupon`、`redeem`、`other`
- `action`: `view`、`click`、`claim`、`redeem`、`lock`、`commit`、`cancel`、`error`、`other`
- `source`: `redeem`、`flash`、`unknown`
- `result`: `success`、`failed`、`invalid`、`not_found`、`not_active`、`sold_out`、`rate_limited`、`error`

## 前端页面设计

### 商品页

新增“活动与优惠”模块：

- 百亿补贴：显示补贴标签和折扣。
- 秒杀券：显示当前是否开启、折扣、库存、领取入口。
- 整点抢金币：显示当前是否可抢、奖励金币、剩余库存或下一轮提示。
- 看广告赚金币：显示阶段奖励，并引导到广告播放。
- 我的可用券：展示当前用户 `pending` 券。
- 金币兑换券：展示兑换门槛和当前金币余额是否满足。

### Rewards 页

新增两块：

- “我的券”：显示 code、折扣、状态、来源、金币成本。
- “当前活动”：显示秒杀券、整点抢金币、金币兑换券入口。

### 购物车页

在优惠券输入框附近展示用户可用券，用户可以复制或输入券码，完成 coupon validate -> checkout -> commit/cancel 链路。

## 压测与故障注入

新增 `scripts/run-loadtest-k8s.sh`：

- 支持 `--users`、`--spawn-rate`、`--duration`、`--host`、`--namespace`。
- 通过 Kubernetes 环境变量更新 loadgenerator 后滚动重启。
- 支持 `--restore` 恢复默认用户数/时长配置。

新增 `scripts/capture-grafana-screenshots.py` 和 shell wrapper：

- 默认抓取三个核心看板：RewardService overview、广告视频稳定性、商品活动/优惠券闭环。
- 输出到 `docs/screenshots/grafana/<timestamp>/`。
- 如果本机没有 Playwright 或 Grafana 未运行，给出明确错误和下一步命令。

新增 `docs/loadtest-monitoring.md`：

- 本地/集群启动监控。
- 执行压测。
- 查看 Grafana。
- 注入广告观看链路 Chaos。
- 截图核心看板。
- 解释 delay/error 故障下核心指标的预期变化。

## 验收标准

- RewardService 单测覆盖 coupon cancel 不退金币、券回到 pending、生命周期指标、金币支出指标。
- Frontend Go 测试覆盖 `/promotion/summary`、`/coupons` 代理、模板渲染至少不崩。
- Locust 文件通过 Python 编译检查。
- Grafana JSON 通过 `python3 -m json.tool`。
- `scripts/verify-monitoring-assets.sh` 校验新增脚本和看板。
- 分支进度总结文档记录本阶段能力、测试命令、限制和后续建议。
