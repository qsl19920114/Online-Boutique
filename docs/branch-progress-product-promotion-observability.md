# 当前分支开发进度总结：商品活动闭环与可观测压测闭环

日期：2026-05-28

## 背景

本阶段在已完成“视频广告 watch session 强校验”的基础上，继续把能力收敛到“以商品为中心的活动闭环 + 可观测压测闭环”。核心目标是让用户能从商品定位活动和优惠券，让秒杀券/金币兑换/购物车使用券形成闭环，并让压测、Grafana 指标、Chaos 注入和截图流程可复用。

## 已完成

1. 设计与计划
   - 新增 spec：`docs/superpowers/specs/2026-05-28-product-promotion-observability-design.md`
   - 新增计划：`docs/superpowers/plans/2026-05-28-product-promotion-observability.md`

2. Coupon 账务闭环修复
   - 修复 `coupon_cancel.lua`：checkout cancel 只把 `locked -> pending`，不再退金币。
   - 保留后续独立 `coupon/refund` 的设计空间，避免取消结算和退券退款混在一起。
   - RewardService 新增低基数指标：
     - `coupon_lifecycle_total`
     - `coupon_validate_total`
     - `coin_spent_total`
     - `coin_refunded_total`
   - redeem/flash spend、validate、commit、cancel 均补充生命周期埋点。

3. 前端商品活动聚合
   - 新增 `GET /promotion/summary?product_id=...`。
   - 新增 `GET /coupons?status=pending` 代理接口。
   - 商品页新增“活动与优惠”模块，聚合百亿补贴、秒杀券、整点抢金币、看广告赚金币、用户已有券。
   - Rewards 页新增“当前活动”“我的券”，兑换按钮会按金币余额置灰。
   - 购物车优惠券输入框附近展示用户可用券，并支持点击填入券码。
   - Frontend 指标新增：
     - `promotion_summary_view_total{page,has_product,result}`
     - `coupon_list_proxy_total{page,status,result}`
   - 指标 label 继续做白名单/分桶，未引入 `product_id`、`session_id`、`coupon_code` 等高基数字段。

4. 压测与监控闭环
   - Locust 增加：
     - `/promotion/summary`
     - `/rewards`
     - `/coupons?status=pending`
     - `/ads/flash/claim`
     - `/ads/rush/claim`
   - 业务态 `not_active`、`sold_out`、`already_claimed` 按预期用户结果处理，HTTP 5xx 仍作为失败。
   - 新增 Grafana 看板：`docs/grafana/product-promotion-closed-loop.json`。
   - 看板覆盖券生命周期、券校验、金币消耗/退回、商品活动聚合请求、RewardService p95。
   - 新增 Kubernetes 压测脚本：`scripts/run-loadtest-k8s.sh`。
   - 新增 Grafana 截图脚本：
     - `scripts/capture-grafana-screenshots.py`
     - `scripts/capture-grafana-screenshots.sh`
   - 新增操作文档：`docs/loadtest-monitoring.md`。

## 主要提交

- `355a977e` docs: plan product promotion observability loop
- `48446658` fix(rewardservice): close coupon cancel accounting loop
- `0cc7fcc2` feat(frontend): add product promotion summary loop
- `f5dc51a2` feat(observability): add promotion loadtest dashboard loop

## 验证记录

本阶段开发过程中已运行并通过：

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py  # 54 tests
cd src/frontend && go test -count=1 ./...
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache python3 -m py_compile src/loadgenerator/locustfile.py scripts/capture-grafana-screenshots.py
python3 -m json.tool docs/grafana/product-promotion-closed-loop.json >/dev/null
bash -n scripts/run-loadtest-k8s.sh
bash -n scripts/capture-grafana-screenshots.sh
bash -n scripts/verify-monitoring-assets.sh
bash scripts/verify-monitoring-assets.sh  # monitoring assets verified
```

TDD 红灯记录：

- RewardService 新增 cancel 不退金币测试后，旧逻辑失败：redeem cancel 返回 `refund_coins=100`，flash cancel 返回 `refund_coins=10`。
- Frontend 新增 promotion/coupon 测试后，旧逻辑编译失败：`promotionSummaryHandler` 和 `couponsProxyHandler` 不存在。
- Monitoring verifier 新增引用后，在脚本/看板未落地前失败：缺少 `scripts/run-loadtest-k8s.sh`。

## 使用方式

查看商品活动聚合：

```bash
curl "http://127.0.0.1:8080/promotion/summary?product_id=OLJCESPC7Z"
```

运行 Kubernetes 压测：

```bash
./scripts/run-loadtest-k8s.sh --namespace default --users 100 --spawn-rate 10 --duration 20m
```

查看 Grafana：

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000
```

采集截图：

```bash
./scripts/capture-grafana-screenshots.sh --grafana-url http://127.0.0.1:3000 --from now-2h --to now
```

默认输出目录：

```text
docs/screenshots/grafana/<timestamp>/
```

## 已知限制

- 当前环境没有确认 Grafana/Prometheus 正在运行，因此截图脚本已提供闭环能力，但实际截图需要本地或集群 Grafana 可访问后执行。
- 当前 loadgenerator 镜像已经消费 `LOCUST_HOST`、`LOCUST_USERS`、`LOCUST_SPAWN_RATE`、`LOCUST_RUN_TIME`；脚本参数会直接影响目标、用户数、爬坡速率和持续时间。
- `coin_refunded_total` 本阶段预计保持平稳；checkout cancel 已改为不退金币，后续只有独立 refund 流程才应推动该指标。
- `docker-compose.yml` 是进入本阶段前已存在的未提交改动，本阶段未触碰。

## 后续建议

1. 给 checkout 成功/失败接入订单维度审计事件，进一步打通券锁定、核销、取消与订单状态。
2. 后续新增 `POST /coupon/refund` 时，单独设计退款次数、来源、审计日志和反作弊策略。
3. 在真实压测环境执行 delay/error Chaos，对比 `ad-video-stability` 与 `product-promotion-closed-loop` 两张看板截图。
