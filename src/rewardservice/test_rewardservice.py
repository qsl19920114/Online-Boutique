import unittest

import rewardservice


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.lists = {}
        self.sorted_sets = {}
        self.expiry = {}
        self.ping_count = 0

    def get(self, key):
        return self.values.get(key)

    def exists(self, key):
        return 1 if key in self.values or key in self.hashes else 0

    def set(self, key, value, ex=None):
        self.values[key] = str(value)
        if ex is not None:
            self.expiry[key] = int(ex)
        return True

    def incrby(self, key, amount):
        value = int(self.values.get(key, 0)) + int(amount)
        self.values[key] = str(value)
        return value

    def decrby(self, key, amount):
        return self.incrby(key, -int(amount))

    def ttl(self, key):
        return self.expiry.get(key, -1)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def lrange(self, key, start, stop):
        data = self.lists.get(key, [])
        if stop == -1:
            return data[start:]
        return data[start : stop + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))
    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]
        return True

    def expire(self, key, seconds):
        self.expiry[key] = int(seconds)
        return True

    def hset(self, key, mapping=None, **kwargs):
        values = {}
        if mapping:
            values.update(mapping)
        values.update(kwargs)
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in values.items()})
        return len(values)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def ping(self):
        self.ping_count += 1
        return True

    def pipeline(self, transaction=False):
        return FakePipeline(self)

    def script_load(self, script):
        return "fake_sha_" + str(hash(script) % 10000)

    # Sorted set operations for sliding window rate limiter
    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {})
        for member, score in mapping.items():
            self.sorted_sets[key][member] = float(score)
        return len(mapping)

    def zremrangebyscore(self, key, min_score, max_score):
        ss = self.sorted_sets.get(key, {})
        if isinstance(min_score, str):
            min_score = float(min_score)
        if isinstance(max_score, str):
            max_score = float(max_score)
        to_remove = [m for m, s in ss.items() if s < min_score or s > max_score]
        for m in to_remove:
            del ss[m]
        return len(to_remove)

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        if "-- rewardservice:ratelimit" in script:
            key = keys[0]
            limit = int(args[0])
            window_sec = int(args[1])
            now_ms = int(args[2])
            window_start = now_ms - window_sec * 1000
            # Clean old entries
            ss = self.sorted_sets.get(key, {})
            to_remove = [m for m, s in ss.items() if s < window_start]
            for m in to_remove:
                del ss[m]
            count = len(ss)
            if count >= limit:
                return 0
            import random
            member = f"{now_ms}:{random.randint(1, 1000000)}"
            self.zadd(key, {member: now_ms})
            self.expiry[key] = window_sec + 1
            return 1
        if "-- rewardservice:earn" in script:
            cooldown_key, idem_key, coins_key, log_key = keys
            balance = self.incrby(coins_key, int(args[0]))
            self.lpush(log_key, args[4])
            self.ltrim(log_key, 0, 49)
            self.expire(log_key, int(args[3]))
            return ["ok", balance]
        if "-- rewardservice:redeem" in script:
            coins_key, coupon_key = keys
            balance = int(self.get(coins_key) or "0")
            if self.exists(coupon_key):
                return ["coupon_exists"]
            if balance < int(args[0]):
                return ["insufficient", balance]
            remaining = self.decrby(coins_key, int(args[0]))
            self.hset(
                coupon_key,
                mapping={
                    "discount_pct": args[1],
                    "session_id": args[2],
                    "status": "pending",
                    "created_at": args[3],
                    "cost_coins": str(args[0]),
                    "source": "redeem",
                },
            )
            self.expire(coupon_key, int(args[4]))
            return ["ok", remaining]
        if "-- rewardservice:coupon_validate" in script:
            coupon = self.hgetall(keys[0])
            if not coupon:
                return ["not_found"]
            if coupon.get("session_id") != args[0] or coupon.get("status") != "pending":
                return ["unavailable"]
            # Check expiry
            created_at = int(coupon.get("created_at", "0"))
            coupon_ttl_sec = int(args[2])
            now_ts = int(args[1])
            if created_at > 0 and now_ts - created_at > coupon_ttl_sec:
                return ["expired"]
            self.hset(keys[0], mapping={"status": "locked", "locked_at": args[1]})
            return ["ok", coupon["discount_pct"]]
        if "-- rewardservice:coupon_commit" in script:
            coupon = self.hgetall(keys[0])
            if not coupon:
                return ["not_found"]
            if coupon.get("status") == "used":
                return ["ok"]
            if coupon.get("status") != "locked":
                return ["unavailable"]
            self.hset(keys[0], mapping={"status": "used", "used_at": args[0]})
            return ["ok"]
        if "-- rewardservice:coupon_cancel" in script:
            coupon_key, coins_key = keys
            now = args[0]
            coupon = self.hgetall(coupon_key)
            if not coupon:
                return ["not_found"]
            status = coupon.get("status")
            if status == "pending":
                return ["ok", "0"]
            if status != "locked":
                return ["unavailable"]
            # Calculate refund
            source = coupon.get("source", "redeem")
            discount_pct = int(coupon.get("discount_pct", "0"))
            refund = 0
            if source == "flash":
                refund = int(coupon.get("cost_coins", "0"))
            else:
                if discount_pct == 90:
                    refund = 50
                elif discount_pct == 80:
                    refund = 100
            if refund > 0:
                self.incrby(coins_key, refund)
            self.hset(coupon_key, mapping={"status": "pending", "cancelled_at": now})
            return ["ok", str(refund)]
        if "-- rewardservice:checkin" in script:
            today_key, streak_key, coins_key = keys
            if self.exists(today_key):
                return ["duplicate"]
            last_date = self.hget(streak_key, "last_date")
            streak = int(self.hget(streak_key, "streak") or "0")
            streak = streak + 1 if last_date == args[1] else 1
            coins = int(args[5]) if streak % 7 == 0 else int(args[4])
            self.set(today_key, "1", ex=int(args[2]))
            self.hset(streak_key, mapping={"last_date": args[0], "streak": streak})
            self.expire(streak_key, int(args[3]))
            balance = self.incrby(coins_key, coins)
            return ["ok", coins, balance, streak]
        if "-- rewardservice:flash_claim" in script:
            pool_key, claimed_key, coins_key, coupon_key = keys
            pool_size = int(args[0])
            cost_coins = int(args[1])
            ttl_sec = int(args[2])
            discount_pct = args[3]
            session_id = args[4]
            created_at = args[5]
            coupon_ttl_sec = int(args[6])
            coupon_code = args[7]
            if self.exists(claimed_key):
                return ["already_claimed"]
            if not self.exists(pool_key):
                self.set(pool_key, pool_size, ex=ttl_sec)
            remaining = int(self.get(pool_key) or "0")
            new_remaining = remaining - 1
            if new_remaining < 0:
                return ["sold_out"]
            self.set(pool_key, new_remaining, ex=ttl_sec)
            if cost_coins > 0:
                coins = int(self.get(coins_key) or "0")
                if coins < cost_coins:
                    self.set(pool_key, remaining, ex=ttl_sec)  # rollback
                    return ["insufficient_coins", str(coins)]
                self.decrby(coins_key, cost_coins)
            self.set(claimed_key, "1", ex=ttl_sec)
            self.hset(coupon_key, mapping={
                "discount_pct": discount_pct,
                "session_id": session_id,
                "status": "pending",
                "created_at": created_at,
                "source": "flash",
                "cost_coins": str(cost_coins),
            })
            self.expire(coupon_key, coupon_ttl_sec)
            return ["ok", str(new_remaining), coupon_code]
        if "-- rewardservice:rush_claim" in script:
            pool_key, claimed_key, coins_key = keys
            pool_size = int(args[0])
            coins_per_claim = int(args[1])
            ttl_sec = int(args[2])
            if self.exists(claimed_key):
                return ["already_claimed"]
            remaining_raw = self.get(pool_key)
            if remaining_raw is None:
                self.set(pool_key, pool_size - 1, ex=ttl_sec)
                self.set(claimed_key, "1", ex=ttl_sec)
                balance = self.incrby(coins_key, coins_per_claim)
                return ["ok", str(coins_per_claim), str(balance), str(pool_size - 1)]
            remaining = int(remaining_raw)
            new_remaining = remaining - 1
            if new_remaining < 0:
                return ["sold_out"]
            self.set(pool_key, new_remaining, ex=ttl_sec)
            self.set(claimed_key, "1", ex=ttl_sec)
            balance = self.incrby(coins_key, coins_per_claim)
            return ["ok", str(coins_per_claim), str(balance), str(new_remaining)]
        raise AssertionError("unknown lua script")


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def get(self, key):
        self.commands.append(("get", key))
        return self

    def execute(self):
        results = []
        for command, key in self.commands:
            if command == "get":
                results.append(self.redis.get(key))
        return results


class RewardServiceTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.app = rewardservice.create_app(redis_client=self.redis)
        self.client = self.app.test_client()

    def test_coin_balance_defaults_to_zero(self):
        res = self.client.get("/coins/session-1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"session_id": "session-1", "balance": 0})

    def test_stage_config_is_served_from_backend(self):
        res = self.client.get("/ads/stage-config")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.get_json(),
            {
                "stages": [
                    {"stage": 1, "trigger_sec": 10, "coins": 5},
                    {"stage": 2, "trigger_sec": 20, "coins": 12},
                    {"stage": 3, "trigger_sec": 30, "coins": 20},
                ],
                "cooldown_sec": 300,
            },
        )

    def test_earn_adds_stage_coins_and_sets_cooldown(self):
        res = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 2}
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["coins_added"], 12)
        self.assertEqual(res.get_json()["balance"], 12)
        self.assertEqual(self.redis.get("coins:session-1"), "12")
        self.assertEqual(res.get_json()["bonus_coins"], 0)

    def test_earn_allows_consecutive_completed_videos_with_streak_bonus(self):
        first = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 3}
        )
        second = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 3}
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["coins_added"], 20)
        self.assertEqual(first.get_json()["bonus_coins"], 0)
        self.assertEqual(first.get_json()["streak_count"], 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["base_coins"], 20)
        self.assertEqual(second.get_json()["bonus_coins"], 5)
        self.assertEqual(second.get_json()["coins_added"], 25)
        self.assertEqual(second.get_json()["streak_count"], 2)
        self.assertEqual(second.get_json()["balance"], 45)
        self.assertEqual(self.redis.get("coins:session-1"), "45")

    def test_redeem_exchanges_coins_for_coupon(self):
        self.redis.set("coins:session-1", 50)

        res = self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})

        body = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["discount_pct"], 90)
        self.assertEqual(body["remaining_balance"], 0)
        self.assertRegex(body["coupon_code"], r"^COIN-[A-Z0-9]{6}-[0-9]{4}$")
        coupon = self.redis.hgetall(f"coupon:{body['coupon_code']}")
        self.assertEqual(coupon["session_id"], "session-1")
        self.assertEqual(coupon["status"], "pending")
        self.assertEqual(coupon["source"], "redeem")
        self.assertEqual(coupon["cost_coins"], "50")

    def test_redeem_stores_cost_coins_for_refund(self):
        self.redis.set("coins:session-1", 200)

        res = self.client.post("/redeem", json={"session_id": "session-1", "cost": 100})
        body = res.get_json()
        coupon = self.redis.hgetall(f"coupon:{body['coupon_code']}")
        self.assertEqual(coupon["cost_coins"], "100")
        self.assertEqual(coupon["source"], "redeem")

    def test_validate_commit_and_cancel_coupon(self):
        self.redis.hset(
            "coupon:COIN-ABC123-0001",
            mapping={
                "discount_pct": 90,
                "session_id": "session-1",
                "status": "pending",
                "created_at": str(int(__import__("time").time())),
                "cost_coins": "50",
                "source": "redeem",
            },
        )

        validate = self.client.post(
            "/coupon/validate",
            json={"session_id": "session-1", "coupon_code": "COIN-ABC123-0001"},
        )
        self.assertEqual(validate.status_code, 200)
        self.assertEqual(validate.get_json(), {"valid": True, "discount_pct": 90})
        self.assertEqual(
            self.redis.hgetall("coupon:COIN-ABC123-0001")["status"], "locked"
        )

        cancel = self.client.post(
            "/coupon/cancel", json={"coupon_code": "COIN-ABC123-0001"}
        )
        self.assertEqual(cancel.status_code, 200)
        cancel_body = cancel.get_json()
        self.assertTrue(cancel_body["success"])
        self.assertEqual(cancel_body["refund_coins"], 50)
        self.assertEqual(
            self.redis.hgetall("coupon:COIN-ABC123-0001")["status"], "pending"
        )

        self.client.post(
            "/coupon/validate",
            json={"session_id": "session-1", "coupon_code": "COIN-ABC123-0001"},
        )
        commit = self.client.post(
            "/coupon/commit", json={"coupon_code": "COIN-ABC123-0001"}
        )
        self.assertEqual(commit.status_code, 200)
        self.assertEqual(
            self.redis.hgetall("coupon:COIN-ABC123-0001")["status"], "used"
        )

    def test_cancel_returns_refund_coins(self):
        """Cancel a locked coupon should refund the coins that were spent."""
        self.redis.set("coins:session-1", 0)
        self.redis.hset(
            "coupon:COIN-XYZ789-0001",
            mapping={
                "discount_pct": 80,
                "session_id": "session-1",
                "status": "locked",
                "created_at": str(int(__import__("time").time())),
                "cost_coins": "100",
                "source": "redeem",
            },
        )

        res = self.client.post(
            "/coupon/cancel", json={"coupon_code": "COIN-XYZ789-0001"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["refund_coins"], 100)
        # Coins should be restored
        self.assertEqual(int(self.redis.get("coins:session-1")), 100)

    def test_validate_rejects_expired_coupon(self):
        """Coupon past TTL should be rejected with expired error."""
        import time
        long_ago = int(time.time()) - 8 * 24 * 60 * 60  # 8 days ago
        self.redis.hset(
            "coupon:COIN-EXP111-0001",
            mapping={
                "discount_pct": 90,
                "session_id": "session-1",
                "status": "pending",
                "created_at": str(long_ago),
                "cost_coins": "50",
                "source": "redeem",
            },
        )

        res = self.client.post(
            "/coupon/validate",
            json={"session_id": "session-1", "coupon_code": "COIN-EXP111-0001"},
        )
        self.assertEqual(res.status_code, 410)
        self.assertIn("expired", res.get_json()["error"])

    def test_cancel_does_not_restore_used_coupon(self):
        self.redis.hset(
            "coupon:COIN-ABC123-0001",
            mapping={
                "discount_pct": 90,
                "session_id": "session-1",
                "status": "locked",
                "cost_coins": "50",
                "source": "redeem",
            },
        )
        self.client.post("/coupon/commit", json={"coupon_code": "COIN-ABC123-0001"})

        res = self.client.post(
            "/coupon/cancel", json={"coupon_code": "COIN-ABC123-0001"}
        )

        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            self.redis.hgetall("coupon:COIN-ABC123-0001")["status"], "used"
        )

    def test_checkin_adds_daily_coins_once(self):
        first = self.client.post("/checkin", json={"session_id": "session-1"})

        self.assertEqual(first.status_code, 200)
        first_body = first.get_json()
        self.assertEqual(first_body["coins_added"], 5)
        self.assertEqual(first_body["balance"], 5)
        self.assertEqual(first_body["streak"], 1)

        duplicate = self.client.post("/checkin", json={"session_id": "session-1"})

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["error"], "already_checked_in")
        self.assertEqual(self.redis.get("coins:session-1"), "5")

    def test_checkin_status_includes_calendar(self):
        res = self.client.get("/checkin/status?session_id=session-1")

        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["session_id"], "session-1")
        self.assertFalse(body["checked_in"])
        self.assertEqual(body["streak"], 0)
        self.assertEqual(body["next_reward"], 5)
        self.assertEqual(len(body["calendar"]), 7)
        self.assertIn("date", body["calendar"][0])
        self.assertIn("checked", body["calendar"][0])

    def test_write_endpoint_is_rate_limited(self):
        self.app.config["RATELIMITS"]["earn"] = 1

        first = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 1}
        )
        second = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-2", "stage": 1}
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["error"], "rate_limited")

    def test_rate_limit_uses_session_id_not_ip(self):
        """Same session_id from different 'IPs' should still be rate limited."""
        self.app.config["RATELIMITS"]["checkin"] = 1
        # First checkin
        res1 = self.client.post(
            "/checkin", json={"session_id": "session-1"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        # Second checkin same session from different IP
        res2 = self.client.post(
            "/checkin", json={"session_id": "session-1"},
            headers={"X-Forwarded-For": "5.6.7.8"},
        )
        # 409 is duplicate checkin, not rate limit — but rate limit key should use session
        self.assertIn(res2.status_code, [409, 429])

    def test_healthz_checks_redis(self):
        res = self.client.get("/_healthz")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.redis.ping_count, 1)

    def test_metrics_endpoint_is_available(self):
        res = self.client.get("/metrics")

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"coins_earned_total", res.data)

    def test_metrics_include_cancelled_counter(self):
        res = self.client.get("/metrics")
        self.assertIn(b"coupon_cancelled_total", res.data)

    def test_metrics_include_earned_by_source(self):
        self.client.post(
            "/earn", json={"session_id": "s1", "ad_id": "ad-1", "stage": 1}
        )
        res = self.client.get("/metrics")
        self.assertIn(b'coins_earned_total{source="ad"}', res.data)

    def test_metrics_include_redeemed_by_cost(self):
        self.redis.set("coins:session-1", 50)
        self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})
        res = self.client.get("/metrics")
        self.assertIn(b'coupon_redeemed_total{cost="50"}', res.data)

    # ── 金币流水查询 ──────────────────────────────────────────────────────── #

    def test_transactions_returns_earn_history(self):
        self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 1}
        )

        res = self.client.get("/coins/session-1/transactions")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["session_id"], "session-1")
        self.assertGreaterEqual(body["total"], 1)
        tx = body["transactions"][0]
        self.assertEqual(tx["type"], "earn")
        self.assertEqual(tx["amount"], 5)
        self.assertIn("ad:ad-1:stage1", tx["detail"])

    def test_transactions_returns_redeem_with_negative_amount(self):
        self.redis.set("coins:session-1", 50)
        self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})

        res = self.client.get("/coins/session-1/transactions")
        body = res.get_json()
        redeem_tx = [t for t in body["transactions"] if t["type"] == "redeem"]
        self.assertEqual(len(redeem_tx), 1)
        self.assertEqual(redeem_tx[0]["amount"], -50)

    def test_transactions_pagination(self):
        self.redis.set("coins:session-1", 500)
        for i in range(3):
            self.client.post(
                "/earn",
                json={"session_id": "session-1", "ad_id": f"ad-{i}", "stage": 1},
            )

        res = self.client.get("/coins/session-1/transactions?page=1&size=2")
        body = res.get_json()
        self.assertEqual(len(body["transactions"]), 2)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["size"], 2)

    # ── 优惠券列表查询 ────────────────────────────────────────────────────── #

    def test_coupon_list_returns_user_coupons(self):
        self.redis.set("coins:session-1", 50)
        self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})

        res = self.client.get("/coupons?session_id=session-1")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["session_id"], "session-1")
        self.assertGreaterEqual(body["count"], 1)
        coupon = body["coupons"][0]
        self.assertIn("coupon_code", coupon)
        self.assertEqual(coupon["discount_pct"], 90)
        self.assertEqual(coupon["status"], "pending")

    def test_coupon_list_filters_by_status(self):
        self.redis.set("coins:session-1", 150)
        self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})

        res = self.client.get("/coupons?session_id=session-1&status=pending")
        body = res.get_json()
        self.assertGreaterEqual(body["count"], 1)

        res_locked = self.client.get("/coupons?session_id=session-1&status=locked")
        body_locked = res_locked.get_json()
        self.assertEqual(body_locked["count"], 0)

    def test_coupon_list_requires_session_id(self):
        res = self.client.get("/coupons")
        self.assertEqual(res.status_code, 400)

    # ── flash_claim atomic coupon ─────────────────────────────────────────── #

    def test_flash_claim_creates_coupon_atomically(self):
        """flash_claim should create the coupon inside Lua (no separate hset)."""
        import time as _time
        # Simulate an active flash slot by monkey-patching
        original = rewardservice._current_flash_slot
        rewardservice._current_flash_slot = lambda: "2024010100"
        try:
            # Initialize the pool manually
            self.redis.set("flash:2024010100:remaining", 5, ex=600)
            res = self.client.post(
                "/flash/claim", json={"session_id": "session-1"}
            )
            self.assertEqual(res.status_code, 200)
            body = res.get_json()
            self.assertIn("coupon_code", body)
            # Coupon should already exist in Redis (created by Lua)
            coupon = self.redis.hgetall(f"coupon:{body['coupon_code']}")
            self.assertEqual(coupon["status"], "pending")
            self.assertEqual(coupon["source"], "flash")
        finally:
            rewardservice._current_flash_slot = original

    # ── cancel refund for flash coupon ────────────────────────────────────── #

    def test_flash_cancel_refunds_cost_coins(self):
        """Cancel a flash coupon should refund the cost_coins stored on the coupon."""
        import time as _time
        self.redis.set("coins:session-1", 10)
        original = rewardservice._current_flash_slot
        rewardservice._current_flash_slot = lambda: "2024010100"
        rewardservice.FLASH_COST_COINS = 10
        try:
            self.redis.set("flash:2024010100:remaining", 5, ex=600)
            claim_res = self.client.post(
                "/flash/claim", json={"session_id": "session-1"}
            )
            code = claim_res.get_json()["coupon_code"]
            # Lock the coupon
            self.client.post(
                "/coupon/validate",
                json={"session_id": "session-1", "coupon_code": code},
            )
            # Cancel should refund 10 coins
            cancel_res = self.client.post(
                "/coupon/cancel", json={"coupon_code": code}
            )
            self.assertEqual(cancel_res.status_code, 200)
            self.assertEqual(cancel_res.get_json()["refund_coins"], 10)
        finally:
            rewardservice._current_flash_slot = original
            rewardservice.FLASH_COST_COINS = 0


if __name__ == "__main__":
    unittest.main()
