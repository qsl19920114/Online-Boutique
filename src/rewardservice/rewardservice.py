import os
import random
import string
import time
from functools import wraps

from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from redis import Redis


STAGES = [
    {"stage": 1, "trigger_sec": 10, "coins": 5},
    {"stage": 2, "trigger_sec": 20, "coins": 12},
    {"stage": 3, "trigger_sec": 30, "coins": 20},
]
STAGE_COINS = {item["stage"]: item["coins"] for item in STAGES}
COOLDOWN_SEC = 300
COUPON_TTL_SEC = 7 * 24 * 60 * 60
COUPON_RULES = {
    50: 90,
    100: 80,
}

REQUEST_LATENCY = Histogram(
    "reward_request_duration_seconds",
    "Reward service HTTP request duration.",
    ["endpoint"],
)
COINS_EARNED = Counter("coins_earned_total", "Coins earned through ad watching.")
COIN_BALANCE = Gauge("coin_balance_current", "Latest observed coin balance.", ["session_id"])
COUPON_REDEEMED = Counter("coupon_redeemed_total", "Coupons redeemed with coins.")
COUPON_USED = Counter("coupon_used_total", "Coupons committed after checkout.")
COUPON_VALIDATE_FAILED = Counter(
    "coupon_validate_failed_total", "Coupon validation failures."
)
COOLDOWN_REJECTED = Counter("cooldown_rejected_total", "Ad reward cooldown rejections.")


def create_app(redis_client=None):
    app = Flask(__name__)
    app.redis = redis_client or build_redis_client()

    def timed(endpoint):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                with REQUEST_LATENCY.labels(endpoint=endpoint).time():
                    return func(*args, **kwargs)

            return wrapper

        return decorator

    @app.get("/coins/<session_id>")
    @timed("coins")
    def coins(session_id):
        balance = _int_value(app.redis.get(_coins_key(session_id)))
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        return jsonify({"session_id": session_id, "balance": balance})

    @app.get("/ads/stage-config")
    @timed("stage_config")
    def stage_config():
        return jsonify({"stages": STAGES, "cooldown_sec": COOLDOWN_SEC})

    @app.post("/earn")
    @timed("earn")
    def earn():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "")).strip()
        ad_id = str(payload.get("ad_id", "")).strip()
        stage = payload.get("stage")
        if not session_id or not ad_id:
            return jsonify({"error": "session_id and ad_id are required"}), 400
        try:
            stage = int(stage)
        except (TypeError, ValueError):
            return jsonify({"error": "stage must be 1, 2, or 3"}), 400
        if stage not in STAGE_COINS:
            return jsonify({"error": "stage must be 1, 2, or 3"}), 400

        cooldown_key = _cooldown_key(session_id, ad_id)
        if app.redis.get(cooldown_key):
            COOLDOWN_REJECTED.inc()
            remaining = app.redis.ttl(cooldown_key)
            return (
                jsonify(
                    {
                        "error": "ad reward is cooling down",
                        "cooldown_remaining_sec": remaining if remaining > 0 else COOLDOWN_SEC,
                    }
                ),
                429,
            )

        coins_to_add = STAGE_COINS[stage]
        balance = int(app.redis.incrby(_coins_key(session_id), coins_to_add))
        app.redis.set(cooldown_key, "1", ex=COOLDOWN_SEC)
        app.redis.lpush(
            _earn_log_key(session_id),
            f"{int(time.time())}:{ad_id}:stage{stage}:{coins_to_add}coins",
        )
        app.redis.ltrim(_earn_log_key(session_id), 0, 9)
        COINS_EARNED.inc(coins_to_add)
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        return jsonify(
            {
                "coins_added": coins_to_add,
                "balance": balance,
                "stage": stage,
                "max_stage_coins": max(STAGE_COINS.values()),
            }
        )

    @app.post("/redeem")
    @timed("redeem")
    def redeem():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "")).strip()
        try:
            cost = int(payload.get("cost"))
        except (TypeError, ValueError):
            return jsonify({"error": "cost must be 50 or 100"}), 400
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400
        if cost not in COUPON_RULES:
            return jsonify({"error": "cost must be 50 or 100"}), 400

        balance = _int_value(app.redis.get(_coins_key(session_id)))
        if balance < cost:
            return jsonify({"error": "insufficient coins", "balance": balance}), 400

        remaining = int(app.redis.decrby(_coins_key(session_id), cost))
        code = _new_coupon_code()
        app.redis.hset(
            _coupon_key(code),
            mapping={
                "discount_pct": COUPON_RULES[cost],
                "session_id": session_id,
                "status": "pending",
                "created_at": int(time.time()),
            },
        )
        app.redis.expire(_coupon_key(code), COUPON_TTL_SEC)
        COUPON_REDEEMED.inc()
        COIN_BALANCE.labels(session_id=session_id).set(remaining)
        return jsonify(
            {
                "coupon_code": code,
                "discount_pct": COUPON_RULES[cost],
                "remaining_balance": remaining,
            }
        )

    @app.post("/coupon/validate")
    @timed("coupon_validate")
    def coupon_validate():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        session_id = str(payload.get("session_id", "")).strip()
        coupon = app.redis.hgetall(_coupon_key(code))
        if not coupon:
            COUPON_VALIDATE_FAILED.inc()
            return jsonify({"error": "coupon not found"}), 404
        if coupon.get("session_id") != session_id or coupon.get("status") != "pending":
            COUPON_VALIDATE_FAILED.inc()
            return jsonify({"error": "coupon is not available"}), 400

        app.redis.hset(_coupon_key(code), mapping={"status": "locked", "locked_at": int(time.time())})
        return jsonify({"valid": True, "discount_pct": int(coupon["discount_pct"])})

    @app.post("/coupon/commit")
    @timed("coupon_commit")
    def coupon_commit():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        if not app.redis.hgetall(_coupon_key(code)):
            return jsonify({"error": "coupon not found"}), 404
        app.redis.hset(_coupon_key(code), mapping={"status": "used", "used_at": int(time.time())})
        COUPON_USED.inc()
        return jsonify({"success": True})

    @app.post("/coupon/cancel")
    @timed("coupon_cancel")
    def coupon_cancel():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        if not app.redis.hgetall(_coupon_key(code)):
            return jsonify({"error": "coupon not found"}), 404
        app.redis.hset(_coupon_key(code), mapping={"status": "pending"})
        return jsonify({"success": True})

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.get("/_healthz")
    def healthz():
        return "ok"

    return app


def build_redis_client():
    host, port = _redis_host_port(os.getenv("REDIS_ADDR", "redis-cart:6379"))
    return Redis(host=host, port=port, decode_responses=True)


def _redis_host_port(addr):
    if ":" not in addr:
        return addr, 6379
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _int_value(value):
    if value in (None, ""):
        return 0
    return int(value)


def _new_coupon_code():
    letters = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    digits = "".join(random.choices(string.digits, k=4))
    return f"COIN-{letters}-{digits}"


def _coins_key(session_id):
    return f"coins:{session_id}"


def _cooldown_key(session_id, ad_id):
    return f"cooldown:{session_id}:{ad_id}"


def _earn_log_key(session_id):
    return f"earn_log:{session_id}"


def _coupon_key(code):
    return f"coupon:{code}"


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
