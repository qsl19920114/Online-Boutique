# Product Promotion Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a product-centered promotion loop and an observable load-test loop for coupons, activity attribution, video ads, and chaos evaluation.

**Architecture:** Keep rewardservice as the source of coupon and activity truth, add a frontend aggregation API for product pages, and expose bounded Prometheus metrics for lifecycle attribution. Add dashboards, load-test helpers, screenshot automation, and docs without requiring a live Grafana in unit tests.

**Tech Stack:** Flask + Redis Lua + prometheus_client, Go frontend with html/template and httptest, Locust Python, Grafana JSON dashboards, Bash/Python operational scripts.

---

## File Structure

- Modify `src/rewardservice/rewardservice.py`: coupon lifecycle metrics, coin spent/refund metrics, validate/commit/cancel instrumentation.
- Modify `src/rewardservice/lua/coupon_cancel.lua`: make cancel restore `locked -> pending` without refunding coins.
- Modify `src/rewardservice/test_rewardservice.py`: TDD coverage for no-refund cancel and metrics.
- Modify `src/frontend/main.go`: register `/promotion/summary` and `/coupons`.
- Modify `src/frontend/handlers.go`: promotion summary builder, coupon proxy, bounded frontend metrics.
- Modify `src/frontend/templates/product.html`: add “活动与优惠”.
- Modify `src/frontend/templates/rewards.html`: add “我的券” and current activity summary.
- Modify `src/frontend/templates/cart.html`: show usable coupons near coupon input.
- Modify `src/frontend/static/styles/styles.css`: styles for promotion/coupon modules.
- Modify `src/frontend/handlers_reward_test.go`: frontend aggregation and coupon proxy tests.
- Modify `src/loadgenerator/locustfile.py`: product promotion, Rewards, coupon/flash/rush flows.
- Add `docs/grafana/product-promotion-closed-loop.json`: Grafana dashboard.
- Modify `docs/grafana/README.md`: document new dashboard and metrics.
- Modify `scripts/verify-monitoring-assets.sh`: verify new dashboard and scripts.
- Add `scripts/run-loadtest-k8s.sh`: Kubernetes load-test wrapper.
- Add `scripts/capture-grafana-screenshots.py`: Playwright screenshot capture.
- Add `scripts/capture-grafana-screenshots.sh`: shell wrapper.
- Add `docs/loadtest-monitoring.md`: pressure test, dashboard, chaos, screenshot guide.
- Add `docs/branch-progress-product-promotion-observability.md`: branch progress summary.

## Task 1: RewardService Coupon Semantics and Metrics

**Files:**
- Modify: `src/rewardservice/test_rewardservice.py`
- Modify: `src/rewardservice/rewardservice.py`
- Modify: `src/rewardservice/lua/coupon_cancel.lua`

- [ ] **Step 1: Write failing tests**

Add or update tests so the desired behavior is explicit:

```python
def test_cancel_locked_redeem_coupon_does_not_refund_spent_coins(self):
    session_id = "cancel-no-refund"
    self._earn(session_id, 50, stage=1)
    redeem = self.client.post("/redeem", json={"session_id": session_id, "cost_coins": 50})
    code = redeem.get_json()["coupon_code"]
    self.client.post("/coupon/validate", json={"session_id": session_id, "coupon_code": code})

    cancel = self.client.post("/coupon/cancel", json={"coupon_code": code})

    self.assertEqual(cancel.status_code, 200)
    self.assertEqual(cancel.get_json()["status"], "cancelled")
    self.assertEqual(cancel.get_json()["refunded_coins"], 0)
    balance = self.client.get(f"/balance?session_id={session_id}").get_json()["balance"]
    self.assertEqual(balance, 0)
    coupons = self.client.get(f"/coupons?session_id={session_id}&status=pending").get_json()["coupons"]
    self.assertEqual(coupons[0]["code"], code)
```

Add a metrics assertion after redeem/validate/cancel:

```python
body = self.client.get("/metrics").data.decode("utf-8")
self.assertIn('coin_spent_total{source="redeem"} 50.0', body)
self.assertIn('coupon_lifecycle_total{from_status="locked",result="success",source="redeem",to_status="pending"}', body)
self.assertIn('coupon_validate_total{reason="ok",result="success",source="redeem"}', body)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.RewardServiceTest.test_cancel_locked_redeem_coupon_does_not_refund_spent_coins
```

Expected: failure showing refunded coins or balance is still restored by current cancel behavior.

- [ ] **Step 3: Implement minimal RewardService changes**

Add counters in `rewardservice.py`:

```python
COUPON_LIFECYCLE = Counter(
    "coupon_lifecycle_total",
    "Coupon lifecycle transitions by bounded source and status.",
    ["source", "from_status", "to_status", "result"],
)
COUPON_VALIDATE_TOTAL = Counter(
    "coupon_validate_total",
    "Coupon validation attempts by bounded source, result, and reason.",
    ["source", "result", "reason"],
)
COIN_SPENT = Counter("coin_spent_total", "Coins spent by source.", ["source"])
COIN_REFUNDED = Counter("coin_refunded_total", "Coins refunded by source.", ["source"])
```

Use helper functions:

```python
def _metric_source(value):
    if value in {"redeem", "flash"}:
        return value
    return "unknown"

def _metric_result(value):
    if value in {"success", "failed", "invalid", "not_found", "not_active", "sold_out", "rate_limited", "error"}:
        return value
    return "error"
```

Update `redeem` and `flash_claim` to increment `COIN_SPENT` when coins are spent. Update `coupon_validate`, `coupon_commit`, and `coupon_cancel` to increment lifecycle and validate counters using bounded labels.

Update `coupon_cancel.lua` locked branch so it does not refund:

```lua
redis.call("HSET", coupon_key,
  "status", "pending",
  "cancelled_at", now,
  "locked_at", "")
return {"ok", "0"}
```

- [ ] **Step 4: Update FakeRedis branch**

In `test_rewardservice.py`, update the fake `coupon_cancel` Lua implementation to mirror production: no coin increment on cancel, status back to `pending`, returned refund `"0"`.

- [ ] **Step 5: Run RewardService tests and commit**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py
```

Commit:

```bash
git add src/rewardservice/rewardservice.py src/rewardservice/lua/coupon_cancel.lua src/rewardservice/test_rewardservice.py
git commit -m "fix(rewardservice): close coupon cancel accounting loop"
```

## Task 2: Frontend Promotion Summary and Coupon Views

**Files:**
- Modify: `src/frontend/handlers_reward_test.go`
- Modify: `src/frontend/main.go`
- Modify: `src/frontend/handlers.go`
- Modify: `src/frontend/templates/product.html`
- Modify: `src/frontend/templates/rewards.html`
- Modify: `src/frontend/templates/cart.html`
- Modify: `src/frontend/static/styles/styles.css`

- [ ] **Step 1: Write failing frontend tests**

Add tests:

```go
func TestPromotionSummaryIncludesProductActivitiesAndCoupons(t *testing.T) {
    // Use httptest rewardservice returning /balance, /ads/subsidy, /ads/flash/status,
    // /ads/rush/status, and /coupons responses. Call /promotion/summary?product_id=OLJCESPC7Z.
    // Assert product_id, subsidy.active, flash.discount_pct, rush.coins,
    // owned_coupons[0].code, redeem_options length, and coin_balance.
}

func TestCouponsProxyAddsSessionAndStatus(t *testing.T) {
    // Use httptest rewardservice and assert the upstream query has session_id and status=pending.
    // Assert the frontend response contains the upstream coupon payload.
}
```

- [ ] **Step 2: Run focused Go tests and verify RED**

Run:

```bash
cd src/frontend && go test -count=1 ./... -run 'TestPromotionSummary|TestCouponsProxy'
```

Expected: failure because routes/handlers do not exist.

- [ ] **Step 3: Implement routes**

Register routes in `main.go`:

```go
r.HandleFunc("/promotion/summary", fe.promotionSummaryHandler).Methods(http.MethodGet)
r.HandleFunc("/coupons", fe.couponsProxyHandler).Methods(http.MethodGet)
```

- [ ] **Step 4: Implement summary and coupon helpers**

In `handlers.go`, define typed structs for reward coupon list, activity status, and summary response. Add:

```go
func (fe *frontendServer) promotionSummaryHandler(w http.ResponseWriter, r *http.Request)
func (fe *frontendServer) couponsProxyHandler(w http.ResponseWriter, r *http.Request)
func (fe *frontendServer) buildPromotionSummary(ctx context.Context, sessionID string, productID string, page string) promotionSummary
func (fe *frontendServer) getRewardCoupons(ctx context.Context, sessionID string, status string) []rewardCouponView
```

Use existing `rewardGetRaw`/`rewardGet` helpers and return graceful fallback data when rewardservice is unavailable.

- [ ] **Step 5: Render product, rewards, and cart modules**

Pass `promotion_summary` to product, rewards, and cart templates. Add compact HTML modules:

```html
<section class="promotion-panel" data-product-id="{{ .product.Item.Id }}">
  <div class="promotion-panel__header">
    <h2>活动与优惠</h2>
    <span>{{ .promotion_summary.CoinBalance }} 金币</span>
  </div>
  ...
</section>
```

Render coupons with stable fallback text when lists are empty.

- [ ] **Step 6: Add CSS**

Add styles for `.promotion-panel`, `.promotion-grid`, `.coupon-strip`, `.coupon-chip`, and `.activity-pill` in `styles.css`. Keep layouts responsive and avoid nested cards.

- [ ] **Step 7: Run frontend tests and commit**

Run:

```bash
cd src/frontend && go test -count=1 ./...
```

Commit:

```bash
git add src/frontend/main.go src/frontend/handlers.go src/frontend/handlers_reward_test.go src/frontend/templates/product.html src/frontend/templates/rewards.html src/frontend/templates/cart.html src/frontend/static/styles/styles.css
git commit -m "feat(frontend): add product promotion summary loop"
```

## Task 3: Load Test, Grafana, Screenshots, and Docs

**Files:**
- Modify: `src/loadgenerator/locustfile.py`
- Add: `docs/grafana/product-promotion-closed-loop.json`
- Modify: `docs/grafana/README.md`
- Modify: `scripts/verify-monitoring-assets.sh`
- Add: `scripts/run-loadtest-k8s.sh`
- Add: `scripts/capture-grafana-screenshots.py`
- Add: `scripts/capture-grafana-screenshots.sh`
- Add: `docs/loadtest-monitoring.md`

- [ ] **Step 1: Write lightweight verification expectations**

Before implementation, run:

```bash
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache python3 -m py_compile src/loadgenerator/locustfile.py
bash scripts/verify-monitoring-assets.sh
```

Expected after adding references but before files: verification should fail for missing dashboard/script references once the script assertions are introduced.

- [ ] **Step 2: Extend Locust flows**

Add tasks for:

```python
@task(2)
def view_promotion_summary(self):
    product_id = random.choice(PRODUCT_IDS)
    self.client.get(f"/promotion/summary?product_id={product_id}", name="/promotion/summary")

@task(1)
def view_rewards_and_coupons(self):
    self.client.get("/rewards", name="/rewards")
    self.client.get("/coupons?status=pending", name="/coupons")

@task(1)
def claim_flash_or_rush(self):
    self.client.post("/ads/flash/claim", name="/ads/flash/claim", catch_response=True)
    self.client.post("/ads/rush/claim", name="/ads/rush/claim", catch_response=True)
```

Mark business responses such as `not_active`, `sold_out`, and `already_claimed` as successful Locust outcomes, but keep HTTP 5xx as failures.

- [ ] **Step 3: Add Grafana dashboard**

Create `docs/grafana/product-promotion-closed-loop.json` with panels for:

- `sum by (source, from_status, to_status, result) (rate(coupon_lifecycle_total[$__rate_interval]))`
- `sum by (source, result, reason) (rate(coupon_validate_total[$__rate_interval]))`
- `sum by (source) (rate(coin_spent_total[$__rate_interval]))`
- `sum by (source) (rate(coin_refunded_total[$__rate_interval]))`
- `sum(rate(promotion_summary_view_total[$__rate_interval])) by (page, result)`
- `histogram_quantile(0.95, sum by (le, endpoint) (rate(reward_request_duration_seconds_bucket[$__rate_interval])))`

- [ ] **Step 4: Add scripts**

`scripts/run-loadtest-k8s.sh` accepts `--namespace`, `--users`, `--spawn-rate`, `--duration`, `--host`, `--restore`, and updates `deployment/loadgenerator` env vars through `kubectl set env`.

`scripts/capture-grafana-screenshots.py` accepts `--grafana-url`, `--out-dir`, `--from`, `--to`, uses Playwright if installed, and exits with a clear message if Playwright or Grafana is unavailable.

`scripts/capture-grafana-screenshots.sh` calls the Python script.

- [ ] **Step 5: Add docs and monitoring verification**

Document:

- Local monitoring startup.
- Kubernetes monitoring startup.
- Load test commands.
- Chaos commands using `REWARD_WATCH_FAULT_MODE=delay|error`.
- Expected Grafana metric changes.
- Screenshot command and output directory.

Update `scripts/verify-monitoring-assets.sh` to run `bash -n` for shell scripts, `py_compile` for Python screenshot script, `json.tool` for dashboard JSON, and grep rendered manifests for `product-promotion-closed-loop.json`.

- [ ] **Step 6: Run verification and commit**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache python3 -m py_compile src/loadgenerator/locustfile.py scripts/capture-grafana-screenshots.py
python3 -m json.tool docs/grafana/product-promotion-closed-loop.json >/dev/null
bash scripts/verify-monitoring-assets.sh
```

Commit:

```bash
git add src/loadgenerator/locustfile.py docs/grafana/product-promotion-closed-loop.json docs/grafana/README.md scripts/verify-monitoring-assets.sh scripts/run-loadtest-k8s.sh scripts/capture-grafana-screenshots.py scripts/capture-grafana-screenshots.sh docs/loadtest-monitoring.md
git commit -m "feat(observability): add promotion loadtest dashboard loop"
```

## Task 4: Branch Progress Summary and Final Verification

**Files:**
- Add: `docs/branch-progress-product-promotion-observability.md`

- [ ] **Step 1: Write progress summary**

Include:

- Completed capabilities.
- Key files changed.
- Test commands and exact outcomes.
- Grafana dashboard and screenshot workflow.
- Known limitation: actual screenshots require live Grafana/Prometheus and reachable frontend/rewardservice.
- Note: existing dirty `docker-compose.yml` is unrelated and intentionally untouched.

- [ ] **Step 2: Run full verification**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py
cd src/frontend && go test -count=1 ./...
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache python3 -m py_compile src/loadgenerator/locustfile.py scripts/capture-grafana-screenshots.py
python3 -m json.tool docs/grafana/product-promotion-closed-loop.json >/dev/null
bash scripts/verify-monitoring-assets.sh
git diff --check
```

- [ ] **Step 3: Commit summary**

```bash
git add docs/branch-progress-product-promotion-observability.md
git commit -m "docs: summarize promotion observability branch progress"
```
