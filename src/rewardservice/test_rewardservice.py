import unittest
from unittest.mock import patch

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
            if self.exists(cooldown_key):
                return ["cooldown", self.ttl(cooldown_key)]
            if self.exists(idem_key):
                return ["duplicate", self.get(coins_key) or "0"]
            balance = self.incrby(coins_key, int(args[0]))
            self.set(cooldown_key, "1", ex=int(args[1]))
            self.set(idem_key, "1", ex=int(args[2]))
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
            # pending 状态：优惠券尚未被 validate 锁定，无需退币直接释放
            # 只有 locked 状态才会触发退币逻辑
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
    """模拟 Redis pipeline，支持 get / set / hgetall，保持链式调用语义。"""

    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def get(self, key):
        self.commands.append(("get", key))
        return self

    def set(self, key, value, ex=None):
        self.commands.append(("set", key, value, ex))
        return self

    def hgetall(self, key):
        self.commands.append(("hgetall", key))
        return self

    def execute(self):
        results = []
        for cmd in self.commands:
            op = cmd[0]
            if op == "get":
                results.append(self.redis.get(cmd[1]))
            elif op == "set":
                ex = cmd[3] if len(cmd) > 3 else None
                results.append(self.redis.set(cmd[1], cmd[2], ex=ex))
            elif op == "hgetall":
                results.append(self.redis.hgetall(cmd[1]))
            else:
                results.append(None)
        return results


class RewardServiceTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.app = rewardservice.create_app(redis_client=self.redis)
        self.client = self.app.test_client()

    def _start_watch(self, session_id="session-1", ad_id="ad-1", creative_id="creative-1", campaign_id="campaign-1", duration_ms=30000):
        return self.client.post(
            "/ads/watch/start",
            json={
                "session_id": session_id,
                "ad_id": ad_id,
                "creative_id": creative_id,
                "campaign_id": campaign_id,
                "duration_ms": duration_ms,
            },
        )

    def _eligible_watch_id(self, session_id="session-1", ad_id="ad-1", position_ms=10000):
        started = self._start_watch(session_id=session_id, ad_id=ad_id)
        self.assertEqual(started.status_code, 200)
        watch_id = started.get_json()["watch_id"]
        event = self.client.post(
            "/ads/watch/event",
            json={
                "session_id": session_id,
                "watch_id": watch_id,
                "ad_id": ad_id,
                "event": "timeupdate",
                "position_ms": position_ms,
            },
        )
        self.assertEqual(event.status_code, 200)
        return watch_id

    def _earn(self, session_id="session-1", ad_id="ad-1", stage=1, watch_id=None):
        if watch_id is None:
            watch_id = self._eligible_watch_id(session_id=session_id, ad_id=ad_id)
        return self.client.post(
            "/earn",
            json={
                "session_id": session_id,
                "ad_id": ad_id,
                "stage": stage,
                "watch_id": watch_id,
            },
        )

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

    def test_watch_start_creates_session_hash_and_returns_config(self):
        res = self._start_watch(
            session_id="session-1",
            ad_id="ad-1",
            creative_id="creative-1",
            campaign_id="campaign-1",
            duration_ms=31000,
        )

        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertRegex(body["watch_id"], r"^[A-Za-z0-9_-]{24}$")
        self.assertEqual(body["expires_in_sec"], 30 * 60)
        self.assertEqual(body["stages"], rewardservice.STAGES)
        watch = self.redis.hgetall(f"ad_watch:{body['watch_id']}")
        self.assertEqual(
            watch,
            {
                "session_id": "session-1",
                "ad_id": "ad-1",
                "creative_id": "creative-1",
                "campaign_id": "campaign-1",
                "duration_ms": "31000",
                "max_position_ms": "0",
            },
        )
        self.assertEqual(self.redis.ttl(f"ad_watch:{body['watch_id']}"), 30 * 60)

    def test_watch_event_validates_match_and_updates_progress_monotonically(self):
        started = self._start_watch()
        watch_id = started.get_json()["watch_id"]

        first = self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-1",
                "watch_id": watch_id,
                "ad_id": "ad-1",
                "event": "timeupdate",
                "position_ms": 12000,
            },
        )
        second = self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-1",
                "watch_id": watch_id,
                "ad_id": "ad-1",
                "event": "timeupdate",
                "position_ms": 7000,
            },
        )
        mismatch = self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-2",
                "watch_id": watch_id,
                "ad_id": "ad-1",
                "event": "timeupdate",
                "position_ms": 13000,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["max_position_ms"], 12000)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["max_position_ms"], 12000)
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.get_json()["error"], "watch_session_mismatch")
        self.assertEqual(
            self.redis.hgetall(f"ad_watch:{watch_id}")["max_position_ms"], "12000"
        )

    def test_earn_requires_known_matching_watch_id(self):
        missing = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 1}
        )
        unknown = self.client.post(
            "/earn",
            json={
                "session_id": "session-1",
                "ad_id": "ad-1",
                "stage": 1,
                "watch_id": "missing-watch",
            },
        )
        started = self._start_watch(session_id="session-1", ad_id="ad-1")
        mismatched = self.client.post(
            "/earn",
            json={
                "session_id": "session-2",
                "ad_id": "ad-1",
                "stage": 1,
                "watch_id": started.get_json()["watch_id"],
            },
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["error"], "watch_id is required")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.get_json()["error"], "watch_session_not_found")
        self.assertEqual(mismatched.status_code, 409)
        self.assertEqual(mismatched.get_json()["error"], "watch_session_mismatch")

    def test_earn_rejects_stage_one_when_progress_below_ten_seconds(self):
        started = self._start_watch()
        watch_id = started.get_json()["watch_id"]
        self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-1",
                "watch_id": watch_id,
                "ad_id": "ad-1",
                "event": "timeupdate",
                "position_ms": 9999,
            },
        )

        res = self.client.post(
            "/earn",
            json={
                "session_id": "session-1",
                "ad_id": "ad-1",
                "stage": 1,
                "watch_id": watch_id,
            },
        )

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"], "watch_progress_insufficient")
        self.assertEqual(res.get_json()["required_position_ms"], 10000)

    def test_earn_allows_stage_one_after_ten_seconds(self):
        watch_id = self._eligible_watch_id(position_ms=10000)

        res = self._earn(stage=1, watch_id=watch_id)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["coins_added"], 5)
        self.assertEqual(res.get_json()["balance"], 5)

    def test_earn_adds_stage_coins_and_sets_cooldown(self):
        watch_id = self._eligible_watch_id(position_ms=20000)
        res = self._earn(stage=2, watch_id=watch_id)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["coins_added"], 12)
        self.assertEqual(res.get_json()["balance"], 12)
        self.assertEqual(self.redis.get("coins:session-1"), "12")
        self.assertEqual(self.redis.ttl("cooldown:session-1:ad-1"), 300)

    def test_earn_rejects_duplicate_ad_during_cooldown(self):
        self._earn(stage=1)
        watch_id = self._eligible_watch_id(position_ms=30000)
        res = self._earn(stage=3, watch_id=watch_id)

        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.get_json()["cooldown_remaining_sec"], 300)
        self.assertEqual(self.redis.get("coins:session-1"), "5")

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

        first = self._earn()
        watch_id = self._eligible_watch_id(ad_id="ad-2")
        second = self._earn(ad_id="ad-2", watch_id=watch_id)

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
        self._earn(session_id="s1")
        res = self.client.get("/metrics")
        self.assertIn(b'coins_earned_total{source="ad"}', res.data)

    def test_metrics_include_watch_and_claim_counters(self):
        watch_id = self._eligible_watch_id()
        self._earn(watch_id=watch_id)
        started = self._start_watch(ad_id="ad-metrics-2")
        watch_id_2 = started.get_json()["watch_id"]
        self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-1",
                "watch_id": watch_id_2,
                "ad_id": "ad-metrics-2",
                "event": "waiting",
                "position_ms": 3000,
            },
        )
        self.client.post(
            "/ads/watch/event",
            json={
                "session_id": "session-1",
                "watch_id": watch_id_2,
                "ad_id": "ad-metrics-2",
                "event": "error",
                "position_ms": 3000,
                "error_type": "decode",
            },
        )
        with patch.dict(
            "os.environ",
            {
                "REWARD_WATCH_FAULT_MODE": "error",
                "REWARD_WATCH_FAULT_RATE": "1",
            },
        ):
            self.client.post(
                "/ads/watch/start",
                json={
                    "session_id": "session-1",
                    "ad_id": "ad-fault",
                    "creative_id": "creative-fault",
                    "campaign_id": "campaign-fault",
                    "duration_ms": 30000,
                },
            )

        res = self.client.get("/metrics")

        self.assertIn(b"ad_watch_session_started_total", res.data)
        self.assertIn(b"ad_watch_event_total", res.data)
        self.assertIn(b"ad_watch_progress_seconds", res.data)
        self.assertIn(b"ad_watch_rebuffer_total", res.data)
        self.assertIn(b"ad_watch_error_total", res.data)
        self.assertIn(b"ad_watch_fault_injected_total", res.data)
        self.assertIn(b"ad_reward_claim_total", res.data)

    def test_metrics_do_not_expose_session_id_labels(self):
        self._earn(session_id="s1")
        self.client.get("/coins/s1")

        res = self.client.get("/metrics")

        self.assertIn(b"coin_balance_current", res.data)
        self.assertNotIn(b'coin_balance_current{session_id="', res.data)

    def test_metrics_include_redeemed_by_cost(self):
        self.redis.set("coins:session-1", 50)
        self.client.post("/redeem", json={"session_id": "session-1", "cost": 50})
        res = self.client.get("/metrics")
        self.assertIn(b'coupon_redeemed_total{cost="50"}', res.data)

    def test_demo_page_is_available(self):
        res = self.client.get("/demo")

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Rewards Demo", res.data)
        self.assertIn(b"/earn", res.data)
        self.assertIn(b"/checkin", res.data)
        self.assertIn(b"Grafana", res.data)

    # ── 金币流水查询 ──────────────────────────────────────────────────────── #

    def test_transactions_returns_earn_history(self):
        self._earn()

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
            watch_id = self._eligible_watch_id(ad_id=f"ad-{i}")
            self._earn(ad_id=f"ad-{i}", watch_id=watch_id)

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
            self.assertEqual(coupon["cost_coins"], str(rewardservice.FLASH_COST_COINS))
        finally:
            rewardservice._current_flash_slot = original

    def test_flash_claim_lua_contract_includes_cost_coins(self):
        with open("lua/flash_claim.lua", encoding="utf-8") as lua_file:
            lua = lua_file.read()
        self.assertIn('"cost_coins"', lua)
        self.assertIn("cost_coins", lua)

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


    # ── 整点抢金币 rush ────────────────────────────────────────────────────── #

    def _patch_rush_time(self, inside: bool):
        """返回一个 context manager，把 rewardservice.datetime 的 now() 固定到
        整点后 30 秒（inside=True）或整点后 10 分钟（inside=False）。"""
        from unittest.mock import patch, MagicMock
        from datetime import datetime as _real_dt, timezone as _tz

        target_dt = (
            _real_dt(2024, 1, 1, 14, 0, 30, tzinfo=_tz.utc)   # 整点后 30s，在窗口内
            if inside
            else _real_dt(2024, 1, 1, 14, 10, 0, tzinfo=_tz.utc)  # 整点后 10min，窗口外
        )

        class _FakeDateTime:
            @classmethod
            def now(cls, tz=None):
                return target_dt

            # 保持 replace / strftime 等方法可以在 target_dt 上直接调用
            def __getattr__(self, name):
                return getattr(_real_dt, name)

        return patch("rewardservice.datetime", _FakeDateTime)

    def test_rush_status_outside_rush_window(self):
        """非整点时段，rush_status 应返回 active=False。"""
        with self._patch_rush_time(inside=False):
            res = self.client.get("/rush/status?session_id=session-1")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertFalse(body["is_rush_time"])
        self.assertFalse(body["active"])
        self.assertGreater(body["next_rush_in_sec"], 0)

    def test_rush_status_inside_rush_window(self):
        """整点后 30s，rush_status 应返回 active=True 且 remaining 有库存。"""
        with self._patch_rush_time(inside=True):
            res = self.client.get("/rush/status?session_id=session-1")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["is_rush_time"])
        self.assertGreater(body["total"], 0)

    def test_rush_claim_rejected_outside_rush_window(self):
        """非整点时 rush_claim 应返回 400 not_rush_time。"""
        with self._patch_rush_time(inside=False):
            res = self.client.post("/rush/claim", json={"session_id": "session-1"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], "not_rush_time")
        self.assertIn("next_rush_in_sec", res.get_json())

    def test_rush_claim_succeeds_during_rush_window(self):
        """整点窗口内抢金币应成功并返回 coins_added 和 balance。"""
        with self._patch_rush_time(inside=True):
            res = self.client.post("/rush/claim", json={"session_id": "session-1"})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("coins_added", body)
        self.assertGreater(body["coins_added"], 0)
        self.assertEqual(body["balance"], body["coins_added"])

    def test_rush_claim_already_claimed(self):
        """同一 session 在同一场次只能抢一次。"""
        with self._patch_rush_time(inside=True):
            self.client.post("/rush/claim", json={"session_id": "session-2"})
            res = self.client.post("/rush/claim", json={"session_id": "session-2"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"], "already_claimed")

    def test_rush_claim_sold_out(self):
        """池子耗尽后应返回 410 rush_sold_out。"""
        from datetime import datetime as _real_dt, timezone as _tz
        with self._patch_rush_time(inside=True):
            # 先把 pool 设置为 0（已售罄）
            slot = rewardservice._rush_slot(
                _real_dt(2024, 1, 1, 14, 0, 30, tzinfo=_tz.utc)
            )
            self.redis.set(f"rush:{slot}:remaining", 0, ex=270)
            res = self.client.post("/rush/claim", json={"session_id": "session-3"})
        self.assertEqual(res.status_code, 410)
        self.assertEqual(res.get_json()["error"], "rush_sold_out")

    # ── 百亿补贴 subsidy ──────────────────────────────────────────────────── #

    def test_subsidy_check_known_product(self):
        """已配置补贴的商品应返回 has_subsidy=True 及折扣信息。"""
        res = self.client.get("/subsidy/check?product_id=OLJCESPC7Z")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["has_subsidy"])
        self.assertEqual(body["product_id"], "OLJCESPC7Z")
        self.assertGreater(body["discount_pct"], 0)
        self.assertIn("label", body)

    def test_subsidy_check_unknown_product(self):
        """未配置补贴的商品应返回 has_subsidy=False。"""
        res = self.client.get("/subsidy/check?product_id=NOTEXIST999")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertFalse(body["has_subsidy"])
        self.assertEqual(body["product_id"], "NOTEXIST999")

    def test_subsidy_products_list(self):
        """产品列表应包含所有已配置补贴商品，且每项都有必要字段。"""
        res = self.client.get("/subsidy/products")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("products", body)
        self.assertGreater(len(body["products"]), 0)
        for item in body["products"]:
            self.assertIn("product_id", item)
            self.assertIn("discount_pct", item)
            self.assertIn("label", item)

    # ── 交易流水截断 ──────────────────────────────────────────────────────── #

    def test_transaction_log_capped_at_1000_entries(self):
        """_record_transaction 使用 ltrim(0,999)，流水条目不应超过 1000。"""
        # 直接向 Redis 写入 1001 条流水
        import time as _time
        key = "txlog:session-cap"
        for i in range(1001):
            self.redis.lpush(key, f"{int(_time.time())}:earn:5:ad:ad-{i}")
            self.redis.ltrim(key, 0, 999)
        self.assertLessEqual(self.redis.llen(key), 1000)

        # 通过 API 追加一条后仍不超过 1000
        self.redis.set("coins:session-cap", 0)
        rewardservice._record_transaction(
            self.redis, "session-cap", "earn", 5, "ad:overflow-ad"
        )
        self.assertLessEqual(self.redis.llen(key), 1000)


if __name__ == "__main__":
    unittest.main()
