-- rewardservice:coupon_commit
local coupon_key = KEYS[1]
local used_at = ARGV[1]

if redis.call("EXISTS", coupon_key) == 0 then
  return {"not_found"}
end

local status = redis.call("HGET", coupon_key, "status")
if status == "used" then
  return {"ok"}
end
if status ~= "locked" then
  return {"unavailable"}
end

redis.call("HSET", coupon_key, "status", "used", "used_at", used_at)
return {"ok"}
