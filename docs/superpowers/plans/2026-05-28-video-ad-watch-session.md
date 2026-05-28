# Video Ad Watch Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real video ad metadata, browser playback event tracking, RewardService watch-session verification, chaos metrics, load simulation, and coupon fixes.

**Architecture:** AdService returns stable video metadata through the existing `Ad` proto. Frontend plays the selected video in the reward modal and proxies watch session/event/reward calls to RewardService. RewardService stores watch-session state in Redis and verifies playback progress before awarding coins.

**Tech Stack:** Proto3, Java/Gradle gRPC AdService, Go frontend, Python Flask RewardService, Redis Lua scripts, Prometheus metrics, Locust, Grafana JSON.

---

## File Structure

- `protos/demo.proto`: add new `Ad` fields.
- `src/adservice/src/main/proto/demo.proto`: copied proto for Gradle builds.
- `src/adservice/src/main/java/hipstershop/AdService.java`: hard-code video metadata on ads.
- `src/frontend/genproto/demo.pb.go`: generated Go proto with new fields.
- `src/frontend/genproto/demo_grpc.pb.go`: regenerate alongside proto.
- `src/frontend/handlers.go`: add watch start/event proxy handlers, forward `watch_id` in `/ads/watch`, expose proxy metrics.
- `src/frontend/main.go`: add `/ads/watch/start` and `/ads/watch/event` routes.
- `src/frontend/templates/ad.html`: render video metadata and use `<video>` events.
- `src/frontend/static/styles/styles.css`: only if the modal needs minor video styling.
- `src/frontend/handlers_reward_test.go`: focused Go handler/metrics tests.
- `src/rewardservice/rewardservice.py`: add watch session APIs, validation, metrics, chaos hooks, coupon response fields.
- `src/rewardservice/lua/flash_claim.lua`: store `cost_coins`.
- `src/rewardservice/test_rewardservice.py`: TDD coverage for watch sessions, earn validation, metrics, flash coupon fix.
- `src/loadgenerator/locustfile.py`: add ad watch and redeem behavior.
- `docs/grafana/ad-video-stability.json`: new dashboard.
- `docs/grafana/README.md`: mention new dashboard and metrics.
- `scripts/verify-monitoring-assets.sh`: include new dashboard validation if needed.
- `docs/branch-progress-video-ad-watch-session.md`: final branch progress summary.

## Task 1: Proto And AdService Video Metadata

**Files:**
- Modify: `protos/demo.proto`
- Modify: `src/adservice/src/main/proto/demo.proto`
- Modify: `src/adservice/src/main/java/hipstershop/AdService.java`
- Regenerate: `src/frontend/genproto/demo.pb.go`
- Regenerate: `src/frontend/genproto/demo_grpc.pb.go`

- [ ] **Step 1: Write the proto expectation**

Add the new fields to `message Ad` in `protos/demo.proto`:

```proto
message Ad {
    string redirect_url = 1;
    string text = 2;
    string ad_id = 3;
    string creative_id = 4;
    string video_url = 5;
    string poster_url = 6;
    int32 duration_ms = 7;
    string campaign_id = 8;
}
```

- [ ] **Step 2: Copy proto to adservice**

Run:

```bash
cp protos/demo.proto src/adservice/src/main/proto/demo.proto
```

Expected: `git diff -- protos/demo.proto src/adservice/src/main/proto/demo.proto` shows the same `Ad` fields in both files.

- [ ] **Step 3: Regenerate frontend Go proto**

Run:

```bash
cd src/frontend && ./genproto.sh
```

Expected: `src/frontend/genproto/demo.pb.go` includes `AdId`, `CreativeId`, `VideoUrl`, `PosterUrl`, `DurationMs`, and `CampaignId`.

- [ ] **Step 4: Update AdService ads**

In `src/adservice/src/main/java/hipstershop/AdService.java`, update each `Ad.newBuilder()` to set stable metadata. Use bounded IDs and public demo videos:

```java
.setAdId("ad-hairdryer-001")
.setCreativeId("creative-hairdryer-video-001")
.setVideoUrl("https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4")
.setPosterUrl("/static/img/products/hairdryer.jpg")
.setDurationMs(30000)
.setCampaignId("campaign-reward-video-demo")
```

Use product-specific IDs and poster paths for each existing ad.

- [ ] **Step 5: Compile AdService**

Run:

```bash
cd src/adservice && ./gradlew test
```

Expected: Gradle compiles generated proto classes and tests pass or reports no tests.

- [ ] **Step 6: Commit task**

```bash
git add protos/demo.proto src/adservice/src/main/proto/demo.proto src/adservice/src/main/java/hipstershop/AdService.java src/frontend/genproto/demo.pb.go src/frontend/genproto/demo_grpc.pb.go
git commit -m "feat(ads): add video metadata to ad proto"
```

## Task 2: RewardService Watch Sessions And Eligibility

**Files:**
- Modify: `src/rewardservice/rewardservice.py`
- Modify: `src/rewardservice/lua/flash_claim.lua`
- Modify: `src/rewardservice/test_rewardservice.py`

- [ ] **Step 1: Write failing RewardService tests**

Add tests to `RewardServiceTest`:

```python
def test_watch_start_creates_session(self):
    res = self.client.post("/ads/watch/start", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "duration_ms": 30000,
    })
    self.assertEqual(res.status_code, 200)
    body = res.get_json()
    self.assertRegex(body["watch_id"], r"^watch_[A-Za-z0-9]+$")
    watch = self.redis.hgetall(f"ad_watch:{body['watch_id']}")
    self.assertEqual(watch["session_id"], "session-1")
    self.assertEqual(watch["ad_id"], "ad-watch-001")

def test_watch_event_updates_max_position(self):
    start = self.client.post("/ads/watch/start", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "duration_ms": 30000,
    }).get_json()
    res = self.client.post("/ads/watch/event", json={
        "session_id": "session-1",
        "watch_id": start["watch_id"],
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "event": "timeupdate",
        "position_ms": 12400,
        "duration_ms": 30000,
    })
    self.assertEqual(res.status_code, 200)
    self.assertEqual(res.get_json()["max_position_ms"], 12400)

def test_earn_requires_sufficient_watch_progress(self):
    start = self.client.post("/ads/watch/start", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "duration_ms": 30000,
    }).get_json()
    self.client.post("/ads/watch/event", json={
        "session_id": "session-1",
        "watch_id": start["watch_id"],
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "event": "timeupdate",
        "position_ms": 9000,
        "duration_ms": 30000,
    })
    res = self.client.post("/earn", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "stage": 1,
        "watch_id": start["watch_id"],
    })
    self.assertEqual(res.status_code, 409)
    self.assertEqual(res.get_json()["error"], "watch_progress_insufficient")

def test_earn_with_sufficient_watch_progress_awards_coins(self):
    start = self.client.post("/ads/watch/start", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "duration_ms": 30000,
    }).get_json()
    self.client.post("/ads/watch/event", json={
        "session_id": "session-1",
        "watch_id": start["watch_id"],
        "ad_id": "ad-watch-001",
        "creative_id": "creative-watch-001",
        "campaign_id": "campaign-demo",
        "event": "timeupdate",
        "position_ms": 10500,
        "duration_ms": 30000,
    })
    res = self.client.post("/earn", json={
        "session_id": "session-1",
        "ad_id": "ad-watch-001",
        "stage": 1,
        "watch_id": start["watch_id"],
    })
    self.assertEqual(res.status_code, 200)
    self.assertEqual(res.get_json()["coins_added"], 5)
```

Add a test that real flash coupons store `cost_coins` by asserting the fake branch and Lua contract both use the same field.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py
```

Expected: failures for missing `/ads/watch/start`, `/ads/watch/event`, `watch_id` validation, and possibly flash `cost_coins`.

- [ ] **Step 3: Implement watch session helpers**

Add constants and helpers in `rewardservice.py`:

```python
WATCH_SESSION_TTL_SEC = 30 * 60
WATCH_ID_TTL_SEC = WATCH_SESSION_TTL_SEC
VALID_AD_EVENTS = {"loadedmetadata", "playing", "timeupdate", "waiting", "ended", "error", "pause", "resume"}

def _watch_key(watch_id):
    return f"ad_watch:{watch_id}"

def _new_watch_id():
    return "watch_" + "".join(random.choices(string.ascii_letters + string.digits, k=20))

def _safe_label(value, default="unknown"):
    text = str(value or "").strip()
    return text if text else default
```

- [ ] **Step 4: Add metrics**

Define bounded metrics:

```python
AD_WATCH_STARTED = Counter("ad_watch_session_started_total", "Ad watch sessions started.", ["ad_id", "creative_id", "campaign_id", "result"])
AD_WATCH_EVENT = Counter("ad_watch_event_total", "Ad watch playback events.", ["event", "ad_id", "creative_id", "campaign_id", "result"])
AD_WATCH_PROGRESS = Histogram("ad_watch_progress_seconds", "Observed ad watch progress.", ["ad_id", "creative_id", "campaign_id"])
AD_WATCH_REBUFFER = Counter("ad_watch_rebuffer_total", "Ad playback rebuffer events.", ["ad_id", "creative_id", "campaign_id"])
AD_WATCH_ERROR = Counter("ad_watch_error_total", "Ad playback errors.", ["ad_id", "creative_id", "campaign_id", "error_type"])
AD_REWARD_CLAIM = Counter("ad_reward_claim_total", "Ad reward claim attempts.", ["ad_id", "creative_id", "campaign_id", "stage", "result"])
AD_WATCH_FAULT = Counter("ad_watch_fault_injected_total", "Injected ad watch faults.", ["mode", "path"])
```

- [ ] **Step 5: Implement `/ads/watch/start`**

Validate required fields, create `watch_id`, write `ad_watch:{watch_id}` hash, set TTL, increment metrics, and return stage config.

- [ ] **Step 6: Implement `/ads/watch/event`**

Validate session/watch/ad match, validate event name, update max position monotonically, update event counters, set `ended/rebuffer_count/error_count`, and keep TTL fresh.

- [ ] **Step 7: Extend `/earn` validation**

Require `watch_id` for ad rewards. Before Lua `earn`, load watch hash and validate session, ad, and progress against `STAGES`. Increment `AD_REWARD_CLAIM` with `success`, `insufficient_progress`, `not_found`, `mismatch`, `cooldown`, or `duplicate`.

- [ ] **Step 8: Fix flash coupon Lua**

In `src/rewardservice/lua/flash_claim.lua`, include:

```lua
"cost_coins",
tostring(cost_coins)
```

inside the `HSET` that creates the coupon.

- [ ] **Step 9: Run RewardService tests**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py
```

Expected: all tests pass.

- [ ] **Step 10: Commit task**

```bash
git add src/rewardservice/rewardservice.py src/rewardservice/lua/flash_claim.lua src/rewardservice/test_rewardservice.py
git commit -m "feat(rewardservice): verify video ad watch sessions"
```

## Task 3: Frontend Watch Proxy And Video Modal

**Files:**
- Modify: `src/frontend/main.go`
- Modify: `src/frontend/handlers.go`
- Modify: `src/frontend/templates/ad.html`
- Modify: `src/frontend/static/styles/styles.css` if needed
- Create: `src/frontend/handlers_reward_test.go`

- [ ] **Step 1: Write failing frontend handler tests**

Create `src/frontend/handlers_reward_test.go` with tests for:

```go
func TestWatchAdForwardsWatchID(t *testing.T) { /* reward server asserts watch_id */ }
func TestWatchStartProxyAddsSessionID(t *testing.T) { /* proxy to /ads/watch/start */ }
func TestWatchEventProxyAddsSessionID(t *testing.T) { /* proxy to /ads/watch/event */ }
func TestMetricsHandlerIncludesWatchProxyCounters(t *testing.T) { /* /metrics includes names */ }
```

Use `httptest.NewServer` as a fake RewardService and set `frontendServer{rewardServiceAddr: server.Listener.Addr().String()}`. Create requests with a context containing `ctxKeySessionID{}`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd src/frontend && go test ./...
```

Expected: new tests fail because routes/types/metrics do not exist yet.

- [ ] **Step 3: Add DTOs and proxy handlers**

Add request structs:

```go
type rewardWatchStartRequest struct {
    SessionID  string `json:"session_id"`
    AdID       string `json:"ad_id"`
    CreativeID string `json:"creative_id"`
    CampaignID string `json:"campaign_id"`
    DurationMS int    `json:"duration_ms"`
}

type rewardWatchEventRequest struct {
    SessionID  string `json:"session_id"`
    WatchID    string `json:"watch_id"`
    AdID       string `json:"ad_id"`
    CreativeID string `json:"creative_id"`
    CampaignID string `json:"campaign_id"`
    Event      string `json:"event"`
    PositionMS int    `json:"position_ms"`
    DurationMS int    `json:"duration_ms"`
    ErrorType  string `json:"error_type"`
}
```

Add handlers:

- `watchAdStartHandler`
- `watchAdEventHandler`

They decode JSON, inject `SessionID: sessionID(r)`, call RewardService, mirror status codes, and increment proxy counters.

- [ ] **Step 4: Register routes**

In `main.go` add before `/ads/watch`:

```go
r.HandleFunc(baseUrl+"/ads/watch/start", svc.watchAdStartHandler).Methods(http.MethodPost)
r.HandleFunc(baseUrl+"/ads/watch/event", svc.watchAdEventHandler).Methods(http.MethodPost)
```

- [ ] **Step 5: Forward `watch_id` in `/ads/watch`**

Extend the payload and `rewardEarnRequest` with `WatchID string`.

- [ ] **Step 6: Add metrics output**

Add `# TYPE` lines and counters for:

- `ad_watch_start_proxy_total`
- `ad_watch_event_proxy_total`
- `ad_watch_reward_proxy_total`

- [ ] **Step 7: Update `ad.html` markup**

Add stable ad data fields to all reward buttons:

```html
data-ad-id="{{.ad.AdId}}"
data-creative-id="{{.ad.CreativeId}}"
data-campaign-id="{{.ad.CampaignId}}"
data-video-url="{{.ad.VideoUrl}}"
data-poster-url="{{.ad.PosterUrl}}"
data-duration-ms="{{.ad.DurationMs}}"
```

Keep fallback expressions where fields may be empty.

- [ ] **Step 8: Replace modal media with video**

Inside modal media area, add:

```html
<video id="reward-ad-video" class="reward-ad-video" playsinline preload="metadata"></video>
```

The JS should start a watch session, set `video.src` and `video.poster`, listen for playback events, send throttled `timeupdate`, unlock stage prompts by `video.currentTime`, and submit `/ads/watch` with `watch_id`.

- [ ] **Step 9: Run frontend tests**

Run:

```bash
cd src/frontend && go test ./...
```

Expected: all frontend tests pass.

- [ ] **Step 10: Commit task**

```bash
git add src/frontend/main.go src/frontend/handlers.go src/frontend/templates/ad.html src/frontend/static/styles/styles.css src/frontend/handlers_reward_test.go
git commit -m "feat(frontend): play tracked video reward ads"
```

## Task 4: Load Generator And Grafana

**Files:**
- Modify: `src/loadgenerator/locustfile.py`
- Create: `docs/grafana/ad-video-stability.json`
- Modify: `docs/grafana/README.md`
- Modify: `scripts/verify-monitoring-assets.sh`

- [ ] **Step 1: Add Locust ad watch task**

In `src/loadgenerator/locustfile.py`, add a function that calls:

```python
start = l.client.post("/ads/watch/start", json={
    "ad_id": random.choice(["ad-hairdryer-001", "ad-watch-001", "ad-mug-001"]),
    "creative_id": "creative-load-demo",
    "campaign_id": "campaign-reward-video-demo",
    "duration_ms": 30000,
})
watch_id = start.json().get("watch_id")
l.client.post("/ads/watch/event", json={... "event": "timeupdate", "position_ms": 12000 ...})
l.client.post("/ads/watch", json={"ad_id": ad_id, "stage": 1, "watch_id": watch_id, "style": "load", "show_in": "locust"})
```

Add it to `UserBehavior.tasks` with a moderate weight.

- [ ] **Step 2: Add Grafana dashboard**

Create `docs/grafana/ad-video-stability.json` with panels for:

- Watch starts: `sum(rate(ad_watch_session_started_total[5m]))`
- Reward success rate: `sum(rate(ad_reward_claim_total{result="success"}[5m])) / clamp_min(sum(rate(ad_reward_claim_total[5m])), 0.001)`
- Rebuffer rate: `sum(rate(ad_watch_rebuffer_total[5m]))`
- Error rate: `sum(rate(ad_watch_error_total[5m]))`
- Watch progress p95: `histogram_quantile(0.95, sum(rate(ad_watch_progress_seconds_bucket[5m])) by (le, ad_id, creative_id))`
- Chaos injections: `sum(rate(ad_watch_fault_injected_total[5m])) by (mode, path)`

- [ ] **Step 3: Document dashboard and metrics**

Update `docs/grafana/README.md` to list the new dashboard and the new ad metrics.

- [ ] **Step 4: Verify syntax/assets**

Run:

```bash
python3 -m py_compile src/loadgenerator/locustfile.py
python3 -m json.tool docs/grafana/ad-video-stability.json >/tmp/ad-video-stability.json
bash scripts/verify-monitoring-assets.sh
```

Expected: all commands pass.

- [ ] **Step 5: Commit task**

```bash
git add src/loadgenerator/locustfile.py docs/grafana/ad-video-stability.json docs/grafana/README.md scripts/verify-monitoring-assets.sh
git commit -m "feat(observability): add video ad stability dashboard"
```

## Task 5: Branch Progress Summary And Full Verification

**Files:**
- Create: `docs/branch-progress-video-ad-watch-session.md`

- [ ] **Step 1: Write progress summary**

Create a concise summary covering:

- Completed video ad proto/adservice work.
- Frontend playback and watch event flow.
- RewardService watch session validation.
- Metrics/Grafana/Locust additions.
- Coupon fixes.
- Test commands and results.
- Known limitations and next steps.

- [ ] **Step 2: Run focused verification**

Run:

```bash
cd src/rewardservice && python3 -m unittest test_rewardservice.py
cd ../frontend && go test ./...
cd ../adservice && ./gradlew test
cd ../loadgenerator && python3 -m py_compile locustfile.py
cd ../..
python3 -m json.tool docs/grafana/ad-video-stability.json >/tmp/ad-video-stability.json
bash scripts/verify-monitoring-assets.sh
git status --short --branch
```

Expected: all tests pass and only intentional changes are present.

- [ ] **Step 3: Commit summary**

```bash
git add docs/branch-progress-video-ad-watch-session.md
git commit -m "docs: summarize video ad stability branch progress"
```
