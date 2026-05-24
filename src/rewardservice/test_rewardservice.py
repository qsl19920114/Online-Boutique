import unittest

import rewardservice


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.lists = {}
        self.expiry = {}

    def get(self, key):
        return self.values.get(key)

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

    def test_metrics_endpoint_is_available(self):
        res = self.client.get("/metrics")

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"coins_earned_total", res.data)


if __name__ == "__main__":
    unittest.main()
