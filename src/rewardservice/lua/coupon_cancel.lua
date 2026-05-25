-- rewardservice:coupon_cancel
local coupon_key = KEYS[1]

if redis.call("EXISTS", coupon_key) == 0 then
  return {"not_found"}
end

local status = redis.call("HGET", coupon_key, "status")
if status == "pending" then
  return {"ok"}
end
if status ~= "locked" then
  return {"unavailable"}
end

redis.call("HSET", coupon_key, "status", "pending")
return {"ok"}
