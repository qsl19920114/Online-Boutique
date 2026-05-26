-- rewardservice:redeem
-- 兑换优惠券：扣减金币 + 创建优惠券记录（含 cost_coins 用于取消时退还）
local coins_key = KEYS[1]
local coupon_key = KEYS[2]

local cost = tonumber(ARGV[1])
local discount_pct = ARGV[2]
local session_id = ARGV[3]
local created_at = ARGV[4]
local coupon_ttl_sec = tonumber(ARGV[5])

if redis.call("EXISTS", coupon_key) == 1 then
  return {"coupon_exists"}
end

local balance = tonumber(redis.call("GET", coins_key) or "0")
if balance < cost then
  return {"insufficient", balance}
end

local remaining = redis.call("DECRBY", coins_key, cost)
redis.call(
  "HSET",
  coupon_key,
  "discount_pct",
  discount_pct,
  "session_id",
  session_id,
  "status",
  "pending",
  "created_at",
  created_at,
  "cost_coins",
  tostring(cost),
  "source",
  "redeem"
)
redis.call("EXPIRE", coupon_key, coupon_ttl_sec)

return {"ok", remaining}
