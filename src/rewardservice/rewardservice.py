import os
import random
import string
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from redis import ConnectionPool, Redis


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
CHECKIN_BASE_COINS = 5
CHECKIN_WEEKLY_COINS = 25
CHECKIN_DAY_TTL_SEC = 24 * 60 * 60
CHECKIN_STREAK_TTL_SEC = 30 * 24 * 60 * 60
EARN_IDEMPOTENCY_TTL_SEC = 24 * 60 * 60
EARN_LOG_TTL_SEC = 48 * 60 * 60
TRANSACTION_TTL_SEC = 30 * 24 * 60 * 60  # 30 days
DEFAULT_RATELIMITS = {
    "earn": 20,
    "checkin": 5,
    "redeem": 10,
    "coupon": 30,
    "flash": 10,
    "rush": 10,
}
RATELIMIT_WINDOW_SEC = 60  # sliding window duration
# 秒杀优惠券配置
FLASH_POOL_SIZE = int(os.getenv("FLASH_POOL_SIZE", "50"))
FLASH_DURATION_SEC = int(os.getenv("FLASH_DURATION_SEC", "600"))   # 10分钟
FLASH_DISCOUNT_PCT = int(os.getenv("FLASH_DISCOUNT_PCT", "70"))    # 7折
FLASH_COST_COINS = int(os.getenv("FLASH_COST_COINS", "0"))         # 免费抢
# 整点抢金币配置
RUSH_POOL_SIZE = int(os.getenv("RUSH_POOL_SIZE", "100"))
RUSH_COINS_PER_CLAIM = int(os.getenv("RUSH_COINS_PER_CLAIM", "10"))
RUSH_DURATION_SEC = int(os.getenv("RUSH_DURATION_SEC", "300"))      # 整点后5分钟
# 百亿补贴商品表（product_id: {discount_pct, label}）
SUBSIDY_PRODUCTS = {
    "OLJCESPC7Z": {"discount_pct": 20, "label": "亿补价"},
    "66VCHSJNUP": {"discount_pct": 15, "label": "亿补价"},
    "1YMWWN1N4O": {"discount_pct": 25, "label": "亿补价"},
    "L9ECAV7KIM": {"discount_pct": 18, "label": "限时亿补"},
    "2ZYFJ3GM2N": {"discount_pct": 30, "label": "限时亿补"},
    "0PUK6V6EV0": {"discount_pct": 12, "label": "亿补价"},
    "9SIQT8TOJO": {"discount_pct": 22, "label": "亿补价"},
}

LUA_SCRIPT_NAMES = [
    "earn",
    "redeem",
    "coupon_validate",
    "coupon_commit",
    "coupon_cancel",
    "checkin",
    "ratelimit",
    "flash_claim",
    "rush_claim",
]

# ── Prometheus Metrics ──────────────────────────────────────────────────────── #

REQUEST_LATENCY = Histogram(
    "reward_request_duration_seconds",
    "Reward service HTTP request duration.",
    ["endpoint"],
)
COINS_EARNED = Counter(
    "coins_earned_total",
    "Coins earned by source.",
    ["source"],
)
COIN_BALANCE = Gauge("coin_balance_current", "Latest observed coin balance.", ["session_id"])
COUPON_REDEEMED = Counter(
    "coupon_redeemed_total",
    "Coupons redeemed with coins by cost.",
    ["cost"],
)
COUPON_USED = Counter("coupon_used_total", "Coupons committed after checkout.")
COUPON_CANCELLED = Counter("coupon_cancelled_total", "Coupons cancelled (refund issued).")
COUPON_VALIDATE_FAILED = Counter(
    "coupon_validate_failed_total",
    "Coupon validation failures by reason.",
    ["reason"],
)
COOLDOWN_REJECTED = Counter("cooldown_rejected_total", "Ad reward cooldown rejections.")
RATELIMIT_REJECTED = Counter(
    "ratelimit_rejected_total", "Rate limit rejections by endpoint.", ["endpoint"]
)
CHECKIN_TOTAL = Counter("checkin_total", "Daily check-in attempts.", ["result"])
CHECKIN_STREAK = Histogram("checkin_streak_histogram", "Observed check-in streak.")
WEEKLY_BONUS = Counter("weekly_bonus_total", "Weekly check-in bonuses awarded.")
REDIS_POOL_ACTIVE = Gauge(
    "redis_pool_connections_active", "Best-effort active Redis pool connections."
)
FLASH_CLAIMED = Counter("flash_sale_claimed_total", "Flash sale coupons claimed.")
FLASH_SOLD_OUT = Counter("flash_sale_sold_out_total", "Flash sale sold-out rejections.")
RUSH_CLAIMED = Counter("rush_claimed_total", "On-the-hour coin rush claims.")
RUSH_SOLD_OUT = Counter("rush_sold_out_total", "Rush sold-out rejections.")


def create_app(redis_client=None):
    app = Flask(__name__)
    app.redis = redis_client or build_redis_client()
    app.lua = _load_lua_scripts()
    # Pre-load Lua script SHAs for EVALSHA
    app.lua_shas = {}
    for name, script in app.lua.items():
        app.lua_shas[name] = app.redis.script_load(script)
    app.config["RATELIMITS"] = dict(DEFAULT_RATELIMITS)

    def timed(endpoint):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                with REQUEST_LATENCY.labels(endpoint=endpoint).time():
                    return func(*args, **kwargs)

            return wrapper

        return decorator

    @app.before_request
    def enforce_rate_limit():
        endpoint = _rate_limit_endpoint(request.path, request.method)
        if not endpoint:
            return None
        limit = int(app.config["RATELIMITS"].get(endpoint, 0))
        if limit <= 0:
            return None
        client_id = _rate_limit_identity()
        now_ms = int(time.time() * 1000)
        key = f"ratelimit:{client_id}:{endpoint}"
        allowed = _eval_script(
            app.redis,
            app.lua["ratelimit"],
            [key],
            [limit, RATELIMIT_WINDOW_SEC, now_ms],
        )
        if int(allowed) != 1:
            RATELIMIT_REJECTED.labels(endpoint=endpoint).inc()
            return jsonify({"error": "rate_limited"}), 429
        return None

    # ── 金币余额 ──────────────────────────────────────────────────────────── #

    @app.get("/coins/<session_id>")
    @timed("coins")
    def coins(session_id):
        balance = _int_value(app.redis.get(_coins_key(session_id)))
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        return jsonify({"session_id": session_id, "balance": balance})

    # ── 金币流水 ──────────────────────────────────────────────────────────── #

    @app.get("/coins/<session_id>/transactions")
    @timed("transactions")
    def transactions(session_id):
        page = max(1, int(request.args.get("page", "1")))
        size = min(50, max(1, int(request.args.get("size", "20"))))
        start = (page - 1) * size
        stop = start + size - 1
        log_key = _transaction_key(session_id)
        total = _int_value(app.redis.llen(log_key))
        items = app.redis.lrange(log_key, start, stop)
        result = []
        for item in items:
            parts = item.split(":", 3)
            if len(parts) >= 4:
                result.append({
                    "timestamp": int(parts[0]),
                    "type": parts[1],
                    "amount": int(parts[2]),
                    "detail": parts[3],
                })
            elif len(parts) == 3:
                result.append({
                    "timestamp": int(parts[0]),
                    "type": parts[1],
                    "amount": int(parts[2]),
                    "detail": "",
                })
        return jsonify({
            "session_id": session_id,
            "page": page,
            "size": size,
            "total": total,
            "transactions": result,
        })

    # ── 优惠券列表 ────────────────────────────────────────────────────────── #

    @app.get("/coupons")
    @timed("coupons")
    def coupon_list():
        session_id = str(request.args.get("session_id", "")).strip()
        status_filter = str(request.args.get("status", "")).strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        index_key = _coupon_index_key(session_id)
        codes = app.redis.lrange(index_key, 0, -1)
        coupons = []
        for code in codes:
            coupon_data = app.redis.hgetall(_coupon_key(code))
            if not coupon_data:
                continue
            if status_filter and coupon_data.get("status") != status_filter:
                continue
            coupons.append({
                "coupon_code": code,
                "discount_pct": _int_value(coupon_data.get("discount_pct")),
                "status": coupon_data.get("status", "unknown"),
                "source": coupon_data.get("source", "redeem"),
                "created_at": _int_value(coupon_data.get("created_at")),
                "cost_coins": _int_value(coupon_data.get("cost_coins")),
            })
        return jsonify({
            "session_id": session_id,
            "coupons": coupons,
            "count": len(coupons),
        })

    # ── 广告阶段配置 ──────────────────────────────────────────────────────── #

    @app.get("/ads/stage-config")
    @timed("stage_config")
    def stage_config():
        return jsonify({"stages": STAGES, "cooldown_sec": COOLDOWN_SEC})

    # ── 看广告赚金币 ──────────────────────────────────────────────────────── #

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

        coins_to_add = STAGE_COINS[stage]
        today = _today_string()
        result = _eval_script(
            app.redis,
            app.lua["earn"],
            [
                _cooldown_key(session_id, ad_id),
                _earn_idem_key(session_id, ad_id, stage, today),
                _coins_key(session_id),
                _earn_log_key(session_id),
            ],
            [
                coins_to_add,
                COOLDOWN_SEC,
                EARN_IDEMPOTENCY_TTL_SEC,
                EARN_LOG_TTL_SEC,
                f"{int(time.time())}:{ad_id}:stage{stage}:{coins_to_add}coins",
            ],
        )
        status_name = result[0]
        if status_name == "cooldown":
            COOLDOWN_REJECTED.inc()
            remaining = int(result[1])
            return (
                jsonify(
                    {
                        "error": "ad reward is cooling down",
                        "cooldown_remaining_sec": remaining if remaining > 0 else COOLDOWN_SEC,
                    }
                ),
                429,
            )
        if status_name == "duplicate":
            return (
                jsonify(
                    {
                        "error": "ad reward already claimed",
                        "balance": int(result[1]),
                    }
                ),
                409,
            )

        balance = int(result[1])
        COINS_EARNED.labels(source="ad").inc(coins_to_add)
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        # 记录交易流水
        _record_transaction(app.redis, session_id, "earn", coins_to_add, f"ad:{ad_id}:stage{stage}")
        return jsonify(
            {
                "coins_added": coins_to_add,
                "balance": balance,
                "stage": stage,
                "max_stage_coins": max(STAGE_COINS.values()),
            }
        )

    # ── 兑换优惠券 ────────────────────────────────────────────────────────── #

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

        code = ""
        remaining = 0
        result = None
        for _ in range(3):
            code = _new_coupon_code()
            result = _eval_script(
                app.redis,
                app.lua["redeem"],
                [_coins_key(session_id), _coupon_key(code)],
                [
                    cost,
                    COUPON_RULES[cost],
                    session_id,
                    int(time.time()),
                    COUPON_TTL_SEC,
                ],
            )
            if result[0] != "coupon_exists":
                break
        if result[0] == "insufficient":
            return jsonify({"error": "insufficient coins", "balance": int(result[1])}), 400
        if result[0] != "ok":
            return jsonify({"error": "coupon generation failed"}), 500

        remaining = int(result[1])
        COUPON_REDEEMED.labels(cost=str(cost)).inc()
        COIN_BALANCE.labels(session_id=session_id).set(remaining)
        # 索引优惠券到用户列表
        app.redis.lpush(_coupon_index_key(session_id), code)
        # 记录交易流水
        _record_transaction(app.redis, session_id, "redeem", -cost, f"coupon:{code}")
        return jsonify(
            {
                "coupon_code": code,
                "discount_pct": COUPON_RULES[cost],
                "remaining_balance": remaining,
            }
        )

    # ── 优惠券校验 ────────────────────────────────────────────────────────── #

    @app.post("/coupon/validate")
    @timed("coupon_validate")
    def coupon_validate():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        session_id = str(payload.get("session_id", "")).strip()
        result = _eval_script(
            app.redis,
            app.lua["coupon_validate"],
            [_coupon_key(code)],
            [session_id, int(time.time()), COUPON_TTL_SEC],
        )
        if result[0] == "not_found":
            COUPON_VALIDATE_FAILED.labels(reason="not_found").inc()
            return jsonify({"error": "coupon not found"}), 404
        if result[0] == "expired":
            COUPON_VALIDATE_FAILED.labels(reason="expired").inc()
            return jsonify({"error": "coupon has expired"}), 410
        if result[0] != "ok":
            COUPON_VALIDATE_FAILED.labels(reason="unavailable").inc()
            return jsonify({"error": "coupon is not available"}), 400

        return jsonify({"valid": True, "discount_pct": int(result[1])})

    # ── 优惠券核销 ────────────────────────────────────────────────────────── #

    @app.post("/coupon/commit")
    @timed("coupon_commit")
    def coupon_commit():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        result = _eval_script(
            app.redis,
            app.lua["coupon_commit"],
            [_coupon_key(code)],
            [int(time.time())],
        )
        if result[0] == "not_found":
            return jsonify({"error": "coupon not found"}), 404
        if result[0] != "ok":
            return jsonify({"error": "coupon is not locked"}), 409
        COUPON_USED.inc()
        return jsonify({"success": True})

    # ── 优惠券取消（退金币）───────────────────────────────────────────────── #

    @app.post("/coupon/cancel")
    @timed("coupon_cancel")
    def coupon_cancel():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("coupon_code", "")).strip().upper()
        # 需要从 coupon 中读取 session_id 来退金币
        coupon_data = app.redis.hgetall(_coupon_key(code))
        session_id = coupon_data.get("session_id", "") if coupon_data else ""
        result = _eval_script(
            app.redis,
            app.lua["coupon_cancel"],
            [_coupon_key(code), _coins_key(session_id)],
            [int(time.time())],
        )
        if result[0] == "not_found":
            return jsonify({"error": "coupon not found"}), 404
        if result[0] != "ok":
            return jsonify({"error": "coupon is not locked"}), 409
        refund = int(result[1])
        COUPON_CANCELLED.inc()
        if refund > 0 and session_id:
            COIN_BALANCE.labels(session_id=session_id).set(
                _int_value(app.redis.get(_coins_key(session_id)))
            )
            _record_transaction(app.redis, session_id, "refund", refund, f"coupon_cancel:{code}")
        return jsonify({"success": True, "refund_coins": refund})

    # ── 签到状态 ──────────────────────────────────────────────────────────── #

    @app.get("/checkin/status")
    @timed("checkin_status")
    def checkin_status():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        today = _today_date()
        today_text = _date_string(today)
        yesterday_text = _date_string(today - timedelta(days=1))
        streak_key = _checkin_streak_key(session_id)
        last_date = app.redis.hget(streak_key, "last_date")
        streak = _int_value(app.redis.hget(streak_key, "streak"))
        if last_date not in (today_text, yesterday_text):
            streak = 0

        week_dates = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
        pipe = app.redis.pipeline(transaction=False)
        for item in week_dates:
            pipe.get(_checkin_key(session_id, _date_string(item)))
        checked_values = pipe.execute()
        checked_in = bool(app.redis.get(_checkin_key(session_id, today_text)))
        next_reward = CHECKIN_WEEKLY_COINS if (streak + 1) % 7 == 0 else CHECKIN_BASE_COINS
        return jsonify(
            {
                "session_id": session_id,
                "today": today_text,
                "checked_in": checked_in,
                "streak": streak,
                "next_reward": next_reward,
                "weekly_bonus": next_reward == CHECKIN_WEEKLY_COINS,
                "calendar": [
                    {
                        "date": _date_string(day),
                        "checked": bool(checked_values[index]),
                        "coins": CHECKIN_WEEKLY_COINS
                        if (index + 1) % 7 == 0
                        else CHECKIN_BASE_COINS,
                    }
                    for index, day in enumerate(week_dates)
                ],
            }
        )

    # ── 签到 ──────────────────────────────────────────────────────────────── #

    @app.post("/checkin")
    @timed("checkin")
    def checkin():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        today = _today_date()
        today_text = _date_string(today)
        yesterday_text = _date_string(today - timedelta(days=1))
        result = _eval_script(
            app.redis,
            app.lua["checkin"],
            [
                _checkin_key(session_id, today_text),
                _checkin_streak_key(session_id),
                _coins_key(session_id),
            ],
            [
                today_text,
                yesterday_text,
                CHECKIN_DAY_TTL_SEC,
                CHECKIN_STREAK_TTL_SEC,
                CHECKIN_BASE_COINS,
                CHECKIN_WEEKLY_COINS,
            ],
        )
        if result[0] == "duplicate":
            CHECKIN_TOTAL.labels(result="duplicate").inc()
            return jsonify({"error": "already_checked_in"}), 409
        coins_added = int(result[1])
        balance = int(result[2])
        streak = int(result[3])
        weekly_bonus = coins_added == CHECKIN_WEEKLY_COINS
        CHECKIN_TOTAL.labels(result="success").inc()
        CHECKIN_STREAK.observe(streak)
        if weekly_bonus:
            WEEKLY_BONUS.inc()
        COINS_EARNED.labels(source="checkin").inc(coins_added)
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        _record_transaction(app.redis, session_id, "checkin", coins_added, f"streak:{streak}")
        return jsonify(
            {
                "coins_added": coins_added,
                "balance": balance,
                "streak": streak,
                "weekly_bonus": weekly_bonus,
                "today": today_text,
            }
        )

    # ------------------------------------------------------------------ #
    #  百亿补贴                                                            #
    # ------------------------------------------------------------------ #

    @app.get("/subsidy/check")
    @timed("subsidy_check")
    def subsidy_check():
        product_id = request.args.get("product_id", "").strip()
        if product_id in SUBSIDY_PRODUCTS:
            info = SUBSIDY_PRODUCTS[product_id]
            return jsonify({
                "product_id": product_id,
                "has_subsidy": True,
                "discount_pct": info["discount_pct"],
                "label": info["label"],
            })
        return jsonify({"product_id": product_id, "has_subsidy": False})

    @app.get("/subsidy/products")
    @timed("subsidy_products")
    def subsidy_products():
        return jsonify({
            "products": [
                {"product_id": pid, **info}
                for pid, info in SUBSIDY_PRODUCTS.items()
            ]
        })

    # ------------------------------------------------------------------ #
    #  秒杀优惠券                                                          #
    # ------------------------------------------------------------------ #

    @app.get("/flash/status")
    @timed("flash_status")
    def flash_status():
        session_id = request.args.get("session_id", "").strip()
        slot = _current_flash_slot()
        if not slot:
            return jsonify({
                "active": False,
                "next_at": _next_event_text("flash"),
                "next_in_sec": _seconds_to_next_flash(),
            })
        pool_key = f"flash:{slot}:remaining"
        remaining_raw = app.redis.get(pool_key)
        ttl = app.redis.ttl(pool_key)
        remaining = int(remaining_raw) if remaining_raw is not None else FLASH_POOL_SIZE
        claimed = False
        if session_id:
            claimed = bool(app.redis.get(f"flash:{slot}:claimed:{session_id}"))
        return jsonify({
            "active": remaining > 0,
            "slot": slot,
            "remaining": remaining,
            "total": FLASH_POOL_SIZE,
            "discount_pct": FLASH_DISCOUNT_PCT,
            "cost_coins": FLASH_COST_COINS,
            "ends_in_sec": max(ttl, 0),
            "claimed": claimed,
        })

    @app.post("/flash/claim")
    @timed("flash_claim")
    def flash_claim():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400
        slot = _current_flash_slot()
        if not slot:
            return jsonify({
                "error": "no_active_flash",
                "next_in_sec": _seconds_to_next_flash(),
            }), 400
        pool_key = f"flash:{slot}:remaining"
        claimed_key = f"flash:{slot}:claimed:{session_id}"
        # 预热池（如果尚未初始化）
        if not app.redis.exists(pool_key):
            app.redis.set(pool_key, FLASH_POOL_SIZE, ex=FLASH_DURATION_SEC)
        # 生成优惠券码（在 Lua 之前确定，传入脚本保证原子性）
        code = _new_coupon_code()
        result = _eval_script(
            app.redis,
            app.lua["flash_claim"],
            [pool_key, claimed_key, _coins_key(session_id), _coupon_key(code)],
            [
                FLASH_POOL_SIZE,
                FLASH_COST_COINS,
                FLASH_DURATION_SEC,
                FLASH_DISCOUNT_PCT,
                session_id,
                int(time.time()),
                COUPON_TTL_SEC,
                code,
            ],
        )
        if result[0] == "already_claimed":
            return jsonify({"error": "already_claimed"}), 409
        if result[0] == "sold_out":
            FLASH_SOLD_OUT.inc()
            return jsonify({"error": "sold_out"}), 410
        if result[0] == "insufficient_coins":
            return jsonify({"error": "insufficient_coins", "balance": int(result[1])}), 400
        remaining = int(result[1])
        coupon_code = result[2]
        balance = _int_value(app.redis.get(_coins_key(session_id)))
        FLASH_CLAIMED.inc()
        if FLASH_COST_COINS > 0:
            COINS_EARNED.labels(source="flash_spend").inc(0)  # no-op, just tracking
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        # 索引优惠券到用户列表
        app.redis.lpush(_coupon_index_key(session_id), coupon_code)
        # 记录交易流水
        if FLASH_COST_COINS > 0:
            _record_transaction(app.redis, session_id, "flash_claim", -FLASH_COST_COINS, f"coupon:{coupon_code}")
        return jsonify({
            "coupon_code": coupon_code,
            "discount_pct": FLASH_DISCOUNT_PCT,
            "cost_coins": FLASH_COST_COINS,
            "remaining": remaining,
            "balance": balance,
        })

    # ------------------------------------------------------------------ #
    #  整点抢金币                                                          #
    # ------------------------------------------------------------------ #

    @app.get("/rush/status")
    @timed("rush_status")
    def rush_status():
        session_id = request.args.get("session_id", "").strip()
        now = datetime.now(timezone.utc)
        secs_past = now.minute * 60 + now.second
        is_rush_time = secs_past < RUSH_DURATION_SEC
        slot = _rush_slot(now)
        pool_key = f"rush:{slot}:remaining"
        if is_rush_time:
            remaining_raw = app.redis.get(pool_key)
            remaining = int(remaining_raw) if remaining_raw is not None else RUSH_POOL_SIZE
            ends_in = RUSH_DURATION_SEC - secs_past
            claimed = bool(app.redis.get(f"rush:{slot}:claimed:{session_id}")) if session_id else False
        else:
            remaining = 0
            ends_in = 0
            claimed = False
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        next_in_sec = int((next_hour - now).total_seconds())
        return jsonify({
            "is_rush_time": is_rush_time,
            "active": is_rush_time and remaining > 0,
            "slot": slot,
            "remaining": remaining,
            "total": RUSH_POOL_SIZE,
            "coins_per_claim": RUSH_COINS_PER_CLAIM,
            "ends_in_sec": ends_in,
            "next_rush_in_sec": next_in_sec if not is_rush_time else 0,
            "claimed": claimed,
        })

    @app.post("/rush/claim")
    @timed("rush_claim")
    def rush_claim():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "session_id is required"}), 400
        now = datetime.now(timezone.utc)
        secs_past = now.minute * 60 + now.second
        if secs_past >= RUSH_DURATION_SEC:
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            next_in_sec = int((next_hour - now).total_seconds())
            return jsonify({"error": "not_rush_time", "next_rush_in_sec": next_in_sec}), 400
        slot = _rush_slot(now)
        pool_key = f"rush:{slot}:remaining"
        claimed_key = f"rush:{slot}:claimed:{session_id}"
        ttl = RUSH_DURATION_SEC - secs_past
        result = _eval_script(
            app.redis,
            app.lua["rush_claim"],
            [pool_key, claimed_key, _coins_key(session_id)],
            [RUSH_POOL_SIZE, RUSH_COINS_PER_CLAIM, ttl],
        )
        if result[0] == "already_claimed":
            return jsonify({"error": "already_claimed"}), 409
        if result[0] == "sold_out":
            RUSH_SOLD_OUT.inc()
            return jsonify({"error": "rush_sold_out"}), 410
        coins_added = int(result[1])
        balance = int(result[2])
        remaining = int(result[3])
        RUSH_CLAIMED.inc()
        COINS_EARNED.labels(source="rush").inc(coins_added)
        COIN_BALANCE.labels(session_id=session_id).set(balance)
        _record_transaction(app.redis, session_id, "rush", coins_added, f"slot:{slot}")
        return jsonify({
            "coins_added": coins_added,
            "balance": balance,
            "remaining": remaining,
        })

    # ── 监控 & 健康 ──────────────────────────────────────────────────────── #

    @app.get("/metrics")
    def metrics():
        _observe_redis_pool(app.redis)
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.get("/_healthz")
    def healthz():
        try:
            app.redis.ping()
        except Exception:
            return "redis unavailable", 503
        return "ok"

    @app.get("/demo")
    def demo_page():
        path = os.path.join(os.path.dirname(__file__), "rewards_demo.html")
        with open(path, encoding="utf-8") as demo_file:
            return Response(demo_file.read(), mimetype="text/html")

    return app


# ── 工具函数 ────────────────────────────────────────────────────────────────── #

def build_redis_client():
    host, port = _redis_host_port(os.getenv("REDIS_ADDR", "redis-cart:6379"))
    pool = ConnectionPool(
        host=os.getenv("REDIS_HOST", host),
        port=int(os.getenv("REDIS_PORT", port)),
        max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
        socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "2")),
        socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "3")),
        health_check_interval=int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL_SEC", "30")),
        retry_on_timeout=True,
        decode_responses=True,
    )
    return Redis(connection_pool=pool)


def _load_lua_scripts():
    scripts = {}
    lua_dir = os.path.join(os.path.dirname(__file__), "lua")
    for name in LUA_SCRIPT_NAMES:
        path = os.path.join(lua_dir, f"{name}.lua")
        with open(path, encoding="utf-8") as script_file:
            scripts[name] = script_file.read()
    return scripts


def _eval_script(redis_client, script, keys, args):
    result = redis_client.eval(script, len(keys), *(list(keys) + [str(arg) for arg in args]))
    if isinstance(result, tuple):
        return list(result)
    return result


def _rate_limit_endpoint(path, method):
    if method != "POST":
        return None
    if path == "/earn":
        return "earn"
    if path == "/checkin":
        return "checkin"
    if path == "/redeem":
        return "redeem"
    if path.startswith("/coupon/"):
        return "coupon"
    if path == "/flash/claim":
        return "flash"
    if path == "/rush/claim":
        return "rush"
    return None


def _rate_limit_identity():
    """限流标识：session_id 优先，回退到 IP。"""
    # 尝试从请求体中提取 session_id
    payload = request.get_json(silent=True) or {}
    sid = str(payload.get("session_id", "")).strip()
    if not sid:
        sid = str(request.args.get("session_id", "")).strip()
    if sid:
        return f"s:{sid}"
    # 回退到 IP
    return f"ip:{_client_ip()}"


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _observe_redis_pool(redis_client):
    pool = getattr(redis_client, "connection_pool", None)
    in_use = getattr(pool, "_in_use_connections", None)
    if in_use is not None:
        REDIS_POOL_ACTIVE.set(len(in_use))


def _redis_host_port(addr):
    if ":" not in addr:
        return addr, 6379
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _int_value(value):
    if value in (None, ""):
        return 0
    return int(value)


def _today_date():
    return datetime.now(timezone.utc).date()


def _today_string():
    return _date_string(_today_date())


def _date_string(day):
    return day.strftime("%Y%m%d")


def _new_coupon_code():
    letters = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    digits = "".join(random.choices(string.digits, k=4))
    return f"COIN-{letters}-{digits}"


def _coins_key(session_id):
    return f"coins:{session_id}"


def _cooldown_key(session_id, ad_id):
    return f"cooldown:{session_id}:{ad_id}"


def _earn_idem_key(session_id, ad_id, stage, today):
    return f"earn_idem:{session_id}:{ad_id}:{stage}:{today}"


def _earn_log_key(session_id):
    return f"earn_log:{session_id}"


def _coupon_key(code):
    return f"coupon:{code}"


def _coupon_index_key(session_id):
    return f"coupon_index:{session_id}"


def _checkin_key(session_id, today):
    return f"checkin:{session_id}:{today}"


def _checkin_streak_key(session_id):
    return f"checkin_streak:{session_id}"


def _transaction_key(session_id):
    return f"txlog:{session_id}"


def _record_transaction(redis_client, session_id, tx_type, amount, detail):
    """记录一笔交易流水到 Redis List。"""
    key = _transaction_key(session_id)
    entry = f"{int(time.time())}:{tx_type}:{amount}:{detail}"
    redis_client.lpush(key, entry)
    redis_client.ltrim(key, 0, 999)  # 保留最近 1000 条
    redis_client.expire(key, TRANSACTION_TTL_SEC)


# ── 秒杀 ──────────────────────────────────────────────────────────────────── #

def _current_flash_slot():
    """返回当前活跃秒杀场次标识，不在活跃窗口则返回 None。
    规则：每逢整 2 小时（0,2,4...22时）开始的 FLASH_DURATION_SEC 内有效。"""
    now = datetime.now(timezone.utc)
    if now.hour % 2 != 0:
        return None
    secs_past = now.minute * 60 + now.second
    if secs_past >= FLASH_DURATION_SEC:
        return None
    return now.strftime("%Y%m%d%H")


def _seconds_to_next_flash():
    """距离下次秒杀开始的秒数。"""
    now = datetime.now(timezone.utc)
    next_even_hour = now.replace(minute=0, second=0, microsecond=0)
    if next_even_hour.hour % 2 != 0 or now.minute * 60 + now.second >= FLASH_DURATION_SEC:
        hours_ahead = 2 - (next_even_hour.hour % 2) if next_even_hour.hour % 2 != 0 else 2
        next_even_hour += timedelta(hours=hours_ahead)
    return max(0, int((next_even_hour - now).total_seconds()))


def _next_event_text(kind):
    if kind == "flash":
        secs = _seconds_to_next_flash()
    else:
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        secs = max(0, int((next_hour - now).total_seconds()))
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


# ── 整点抢金币 ─────────────────────────────────────────────────────────────── #

def _rush_slot(now=None):
    """整点场次标识：YYYYMMDDHH。"""
    if now is None:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H")


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
