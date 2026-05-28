# Video Ad Watch Session Design

Date: 2026-05-28
Branch: dev

## Goal

Build a complete video ad playback and stability evaluation path for Online Boutique:

- AdService returns stable video ad metadata.
- Frontend renders a real video player in the reward ad modal.
- RewardService creates watch sessions, records playback events, and verifies watch progress before awarding coins.
- Metrics and Grafana dashboards expose ad playback duration, reward success, rebuffering, errors, and chaos injection impact.
- Locust can simulate high-concurrency ad watching and reward claiming.
- Coupon redemption is enriched where it touches ad rewards, including fixing the flash coupon `cost_coins` Lua gap.

## Scope

In scope:

- Extend `Ad` proto fields with `ad_id`, `creative_id`, `video_url`, `poster_url`, `duration_ms`, and `campaign_id`.
- Regenerate Go, Python, and Java proto outputs using existing repo scripts where available.
- Update `adservice` to populate video metadata from static in-code ads.
- Update frontend templates and handlers to pass metadata into the modal and use `<video>`.
- Add RewardService endpoints:
  - `POST /ads/watch/start`
  - `POST /ads/watch/event`
  - extend `POST /earn` with `watch_id`
- Store watch session state in Redis with TTL.
- Add playback and reward metrics with bounded labels.
- Add optional chaos controls for ad and reward paths through environment variables.
- Extend Locust traffic for ad watch sessions and reward claims.
- Update Grafana dashboards or add a new dashboard for video ad stability.
- Add a branch progress summary document.

Out of scope for this branch:

- A new standalone ad playback service.
- Database-backed campaign management.
- Real CDN upload or media asset pipeline.
- Per-user Prometheus labels such as `session_id` or `watch_id`.
- Cryptographic anti-fraud guarantees. Watch sessions reduce trivial direct `/earn` abuse but do not replace server-side media attestation.

## Existing Context

Current ad metadata is only text and redirect URL:

- `protos/demo.proto`
- `src/adservice/src/main/java/hipstershop/AdService.java`
- generated proto files under each service.

Current frontend reward ad modal is timer-based:

- `src/frontend/templates/ad.html`
- `src/frontend/handlers.go`

Current RewardService only trusts `session_id`, `ad_id`, and `stage`:

- `src/rewardservice/rewardservice.py`
- `src/rewardservice/lua/earn.lua`

Existing metrics are split between:

- RewardService Prometheus client metrics.
- Frontend hand-written `/metrics` counters.
- Checkout hand-written `/metrics` counters.

## Architecture

AdService remains the source of ad selection and creative metadata. It returns one or more ads with stable IDs and video attributes. The first implementation uses hard-coded metadata so the demo stays simple and deployable.

Frontend remains the playback surface. It opens the existing reward modal, loads the chosen ad's video metadata, starts a watch session through RewardService, sends playback events, and submits a reward claim only when the video progress reaches the requested stage.

RewardService owns reward eligibility. It records a watch session keyed by `watch_id`, tracks maximum playback position, event counts, error state, and ended state, then verifies that the requested stage has been reached before running the existing coin-award Lua script.

Prometheus labels stay bounded. Metrics use `ad_id`, `creative_id`, `campaign_id`, `stage`, `event`, `result`, and `reason`. They do not use `session_id` or `watch_id`.

## Data Model

Proto `Ad` gains:

- `ad_id`: stable business ID used for reward cooldown and metrics.
- `creative_id`: stable creative/video ID.
- `video_url`: video source URL.
- `poster_url`: poster or fallback image URL.
- `duration_ms`: expected duration.
- `campaign_id`: campaign grouping for dashboards.

RewardService Redis keys:

- `ad_watch:{watch_id}` hash:
  - `session_id`
  - `ad_id`
  - `creative_id`
  - `campaign_id`
  - `duration_ms`
  - `started_at`
  - `max_position_ms`
  - `last_event`
  - `rebuffer_count`
  - `error_count`
  - `ended`
- `ad_watch_by_session:{session_id}` optional list for debug/history.

TTL:

- Watch sessions expire after 30 minutes.
- Event history is not stored in full by default; aggregate fields are enough for eligibility and metrics.

## API Design

`POST /ads/watch/start`

Request:

```json
{
  "session_id": "session-1",
  "ad_id": "ad-watch-001",
  "creative_id": "creative-watch-001",
  "campaign_id": "spring-sale",
  "duration_ms": 30000
}
```

Response:

```json
{
  "watch_id": "watch_...",
  "expires_in_sec": 1800,
  "stage_config": {
    "stages": [
      {"stage": 1, "trigger_sec": 10, "coins": 5},
      {"stage": 2, "trigger_sec": 20, "coins": 12},
      {"stage": 3, "trigger_sec": 30, "coins": 20}
    ],
    "cooldown_sec": 300
  }
}
```

`POST /ads/watch/event`

Request:

```json
{
  "session_id": "session-1",
  "watch_id": "watch_...",
  "ad_id": "ad-watch-001",
  "creative_id": "creative-watch-001",
  "campaign_id": "spring-sale",
  "event": "timeupdate",
  "position_ms": 12400,
  "duration_ms": 30000,
  "error_type": ""
}
```

Accepted events:

- `loadedmetadata`
- `playing`
- `timeupdate`
- `waiting`
- `ended`
- `error`
- `pause`
- `resume`

Response:

```json
{
  "ok": true,
  "max_position_ms": 12400
}
```

`POST /earn`

Existing fields remain. Add `watch_id`:

```json
{
  "session_id": "session-1",
  "ad_id": "ad-watch-001",
  "stage": 2,
  "watch_id": "watch_..."
}
```

Validation:

- `session_id`, `ad_id`, `stage`, and `watch_id` are required.
- `watch_id` must exist.
- `watch_id.session_id` must match request session.
- `watch_id.ad_id` must match request ad.
- `max_position_ms >= stage.trigger_sec * 1000`.
- Existing cooldown and idempotency checks still apply.

Failure examples:

- `watch_session_required` with 400.
- `watch_session_not_found` with 404.
- `watch_session_mismatch` with 403.
- `watch_progress_insufficient` with 409.
- Existing `cooldown`, `duplicate`, and rate limit failures remain unchanged.

Frontend `/ads/watch` proxy forwards `watch_id` to RewardService.

## Chaos Controls

Use opt-in environment variables, defaulting to off:

- `AD_FAULT_MODE=none|empty|error|delay`
- `AD_FAULT_RATE=0.0..1.0`
- `AD_FAULT_DELAY_MS=0..`
- `REWARD_WATCH_FAULT_MODE=none|error|delay`
- `REWARD_WATCH_FAULT_RATE=0.0..1.0`
- `REWARD_WATCH_FAULT_DELAY_MS=0..`

Fault injection must be scoped to ad playback/reward watch paths and must not affect default golden shopping flows when disabled.

## Metrics

RewardService:

- `ad_watch_session_started_total{ad_id,creative_id,campaign_id,result}`
- `ad_watch_event_total{event,ad_id,creative_id,campaign_id,result}`
- `ad_watch_progress_seconds{ad_id,creative_id,campaign_id}` histogram
- `ad_watch_rebuffer_total{ad_id,creative_id,campaign_id}`
- `ad_watch_error_total{ad_id,creative_id,campaign_id,error_type}`
- `ad_reward_claim_total{ad_id,creative_id,campaign_id,stage,result}`
- `ad_watch_fault_injected_total{mode,path}`

Frontend:

- Keep existing `ad_click_total{style,show_in}` and `ad_watch_complete_total`.
- Add bounded counters for proxy outcomes:
  - `ad_watch_start_proxy_total{result}`
  - `ad_watch_event_proxy_total{event,result}`
  - `ad_watch_reward_proxy_total{result}`

Grafana should show:

- Watch start rate.
- Reward success rate.
- P50/P95/P99 watch progress.
- Rebuffer and error rates by ad/creative.
- Claim failures by reason.
- Chaos injection count vs reward success and playback errors.

## Frontend Behavior

The ad modal uses a `<video>` element with controls disabled for the reward playback path. It should:

- Show poster/fallback content before load.
- Start a watch session before playing.
- Send `loadedmetadata`, `playing`, throttled `timeupdate`, `waiting`, `ended`, and `error`.
- Unlock stage buttons only when `currentTime` reaches stage thresholds.
- Continue to support the existing reward copy and stage UI.
- Fall back to the static poster and a clear retry state if the video fails to load.

Default video URLs are public demo URLs and can be replaced later with real ad CDN URLs.

## Coupon Enhancements

Minimum required coupon work in this branch:

- Fix `flash_claim.lua` to store `cost_coins`.
- Add or adjust tests so FakeRedis and Lua behavior stay aligned.
- Add coupon response fields useful to UI:
  - `valid_until`
  - `source`
  - `min_order_amount` if a rule is introduced.

Additional coupon rule enrichment can be incremental if the video-ad work grows too large.

## Load Testing

Extend `src/loadgenerator/locustfile.py` with tasks that simulate:

- Landing on home or product pages.
- Starting a watch session.
- Sending playback events at multiple positions.
- Claiming a stage reward.
- Visiting rewards and redeeming when enough coins exist.

The load generator should randomize `ad_id`, `creative_id`, user sessions, and stage targets without generating unbounded metric labels.

## Tests

RewardService:

- Watch start creates session.
- Watch event updates max position.
- Insufficient progress rejects `/earn`.
- Sufficient progress allows `/earn`.
- Session mismatch rejects `/earn`.
- Duplicate/cooldown behavior still works.
- Metrics include watch session, event, progress, reward result, and fault injection names.
- Flash coupon stores `cost_coins`.

Frontend:

- `/ads/watch/start` proxy forwards session and returns JSON.
- `/ads/watch/event` proxy forwards structured event.
- `/ads/watch` forwards `watch_id`.
- `/metrics` includes new proxy counters.

AdService:

- Returned ads include stable IDs and video metadata.

Load generator:

- Import/syntax check.
- Ad watch task can run without exceptions.

Grafana/scripts:

- Monitoring asset verification covers the new dashboard.

## Rollout Notes

All new behavior must be default-safe:

- Fault injection disabled by default.
- Existing ad display still works if video metadata is absent.
- Existing reward paths remain compatible enough for a progressive rollout, but stage rewards should require `watch_id` for frontend-generated ad rewards once the frontend is updated.

## Risks

- Regenerating protos across Go, Java, and Python may require language-specific tools already bundled in this repo.
- Public demo video URLs can be unavailable in some environments; tests should not depend on network video download.
- Hand-written frontend metrics are simple but less robust than a full Prometheus client; this branch keeps the local pattern to reduce blast radius.
- Watch session validation reduces direct reward abuse but is still browser-event based.
