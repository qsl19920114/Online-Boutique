-- rewardservice:coupon_validate
-- 校验优惠券：存在性 + 所有权 + 状态 + 过期时间
local coupon_key = KEYS[1]
local session_id = ARGV[1]
local locked_at  = ARGV[2]
local coupon_ttl_sec = tonumber(ARGV[3])

if redis.call("EXISTS", coupon_key) == 0 then
  return {"not_found"}
end

local owner = redis.call("HGET", coupon_key, "session_id")
local status = redis.call("HGET", coupon_key, "status")
if owner ~= session_id or status ~= "pending" then
  return {"unavailable"}
end

-- 业务层过期校验：created_at + coupon_ttl_sec < now 则过期
local created_at = tonumber(redis.call("HGET", coupon_key, "created_at") or "0")
if created_at > 0 and tonumber(locked_at) - created_at > coupon_ttl_sec then
  return {"expired"}
end

local discount_pct = redis.call("HGET", coupon_key, "discount_pct")
redis.call("HSET", coupon_key, "status", "locked", "locked_at", locked_at)

return {"ok", discount_pct}
