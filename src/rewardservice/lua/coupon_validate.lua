-- rewardservice:coupon_validate
local coupon_key = KEYS[1]
local session_id = ARGV[1]
local locked_at = ARGV[2]

if redis.call("EXISTS", coupon_key) == 0 then
  return {"not_found"}
end

local owner = redis.call("HGET", coupon_key, "session_id")
local status = redis.call("HGET", coupon_key, "status")
if owner ~= session_id or status ~= "pending" then
  return {"unavailable"}
end

local discount_pct = redis.call("HGET", coupon_key, "discount_pct")
redis.call("HSET", coupon_key, "status", "locked", "locked_at", locked_at)

return {"ok", discount_pct}
