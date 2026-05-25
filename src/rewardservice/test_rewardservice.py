import unittest

import rewardservice


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.lists = {}
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

    def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        if "-- rewardservice:ratelimit" in script:
            count = self.incrby(keys[0], 1)
            if count == 1:
                self.expire(keys[0], int(args[1]))
            return 1 if count <= int(args[0]) else 0
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
            coupon = self.hgetall(keys[0])
            if not coupon:
                return ["not_found"]
            if coupon.get("status") == "pending":
                return ["ok"]
            if coupon.get("status") != "locked":
                return ["unavailable"]
            self.hset(keys[0], mapping={"status": "pending"})
            return ["ok"]
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
        self.assertEqual(self.redis.ttl("cooldown:session-1:ad-1"), 300)

    def test_earn_rejects_duplicate_ad_during_cooldown(self):
        self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 1}
        )

        res = self.client.post(
            "/earn", json={"session_id": "session-1", "ad_id": "ad-1", "stage": 3}
        )

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

    def test_validate_commit_and_cancel_coupon(self):
        self.redis.hset(
            "coupon:COIN-ABC123-0001",
            mapping={
                "discount_pct": 90,
                "session_id": "session-1",
                "status": "pending",
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

    def test_cancel_does_not_restore_used_coupon(self):
        self.redis.hset(
            "coupon:COIN-ABC123-0001",
            mapping={
                "discount_pct": 90,
                "session_id": "session-1",
                "status": "locked",
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
            "/earn", json={"session_id": "session-2", "ad_id": "ad-2", "stage": 1}
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["error"], "rate_limited")

    def test_healthz_checks_redis(self):
        res = self.client.get("/_healthz")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.redis.ping_count, 1)

    def test_metrics_endpoint_is_available(self):
        res = self.client.get("/metrics")

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"coins_earned_total", res.data)


if __name__ == "__main__":
    unittest.main()
