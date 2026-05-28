# Load Test, Grafana, Screenshots

This runbook covers the Task 3 load-test and monitoring loop for the reward,
promotion, and coupon flows.

## Local Monitoring

Start the local rewardservice demo and monitoring stack from the repository root:

```bash
./scripts/run-reward-demo-local.sh
./scripts/run-monitoring-local-docker.sh
```

Grafana listens on:

```text
http://127.0.0.1:3000
admin / admin
```

Prometheus listens on:

```text
http://127.0.0.1:9090
```

The local Prometheus configuration scrapes both `host.docker.internal:8091`
for RewardService metrics and `host.docker.internal:8080` for frontend
promotion/coupon proxy metrics.

When the full Docker Compose stack is running, use the compose network directly:

```bash
REWARD_TARGET=rewardservice:8080 \
FRONTEND_TARGET=frontend:8080 \
APP_NETWORK=online-boutique_default \
./scripts/run-monitoring-local-docker.sh
```

If the full frontend is running locally, a direct Locust smoke run can generate
traffic against it:

```bash
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache \
  locust -f src/loadgenerator/locustfile.py \
  --host http://127.0.0.1:8080 \
  --headless -u 20 -r 2 -t 10m
```

## Kubernetes Monitoring

Deploy or refresh the self-contained Prometheus and Grafana stack:

```bash
./scripts/deploy-monitoring.sh
```

Open Grafana:

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000
```

Open Prometheus when raw target or query checks are needed:

```bash
kubectl -n monitoring port-forward svc/prometheus 9090:9090
```

The Grafana dashboards are imported from `docs/grafana/*.json`, including:

- `rewardservice-overview`
- `ad-video-stability`
- `product-promotion-closed-loop`

## Kubernetes Load Test

Use `scripts/run-loadtest-k8s.sh` to set loadgenerator environment variables and
restart the deployment:

```bash
./scripts/run-loadtest-k8s.sh \
  --namespace default \
  --users 100 \
  --spawn-rate 10 \
  --duration 20m \
  --host frontend:80
```

The loadgenerator image consumes `FRONTEND_ADDR`/`USERS` and also honors
`LOCUST_HOST`, `LOCUST_USERS`, `LOCUST_SPAWN_RATE`, and `LOCUST_RUN_TIME`, so the
script controls target host, user count, ramp rate, and run duration.

Restore conservative defaults after the test:

```bash
./scripts/run-loadtest-k8s.sh --namespace default --restore
```

## Chaos Checks

Inject reward watch-path delay:

```bash
kubectl -n default set env deployment/rewardservice REWARD_WATCH_FAULT_MODE=delay
kubectl -n default rollout restart deployment/rewardservice
```

Inject reward watch-path errors:

```bash
kubectl -n default set env deployment/rewardservice REWARD_WATCH_FAULT_MODE=error
kubectl -n default rollout restart deployment/rewardservice
```

Remove the fault mode:

```bash
kubectl -n default set env deployment/rewardservice REWARD_WATCH_FAULT_MODE-
kubectl -n default rollout restart deployment/rewardservice
```

## Expected Metric Movement

- `promotion_summary_view_total` increases when Locust calls
  `/promotion/summary?product_id=<id>`.
- `coupon_lifecycle_total` and `coupon_validate_total` move as coupons are
  issued, validated, consumed, rejected, or expire.
- `coin_spent_total` rises on coupon purchases or other spend paths.
- `coin_refunded_total` should stay flat in the checkout cancel path because
  cancel now only restores `locked -> pending`; it is reserved for a future
  explicit refund flow.
- `flash_sale_claimed_total`, `flash_sale_sold_out_total`,
  `rush_claimed_total`, and `rush_sold_out_total` move with flash/rush claim
  attempts.
- `reward_request_duration_seconds` p95 should rise during
  `REWARD_WATCH_FAULT_MODE=delay`.
- `ad_watch_fault_injected_total` should rise during `delay` or `error` chaos.
- Business states such as `not_active`, `sold_out`, and `already_claimed` are
  expected user outcomes in the load test. HTTP 5xx responses should still show
  up as failures.

## Screenshots

Capture the default dashboard set:

```bash
./scripts/capture-grafana-screenshots.sh \
  --grafana-url http://127.0.0.1:3000 \
  --from now-2h \
  --to now
```

Default output:

```text
docs/screenshots/grafana/<timestamp>/
```

The script captures:

- `rewardservice-overview.png`
- `ad-video-stability.png`
- `product-promotion-closed-loop.png`

If Playwright is missing, install it with:

```bash
python3 -m pip install playwright && python3 -m playwright install chromium
```

If Grafana is unreachable, start it locally or run:

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000
```

## Validation

Run the monitoring asset verifier after editing dashboards, scripts, or docs:

```bash
./scripts/verify-monitoring-assets.sh
```

Task 3 verification commands:

```bash
PYTHONPYCACHEPREFIX=/tmp/online-boutique-pycache \
  python3 -m py_compile \
  src/loadgenerator/locustfile.py \
  scripts/capture-grafana-screenshots.py

python3 -m json.tool docs/grafana/product-promotion-closed-loop.json >/dev/null
bash -n scripts/run-loadtest-k8s.sh
bash -n scripts/capture-grafana-screenshots.sh
bash -n scripts/verify-monitoring-assets.sh
bash scripts/verify-monitoring-assets.sh
```
