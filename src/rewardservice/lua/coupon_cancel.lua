-- rewardservice:coupon_cancel
-- 取消锁定优惠券：仅把 locked 回滚为 pending，不退还金币
local coupon_key = KEYS[1]

local now = ARGV[1]

if redis.call("EXISTS", coupon_key) == 0 then
  return {"not_found"}
end

local status = redis.call("HGET", coupon_key, "status")
if status == "pending" then
  return {"ok", "0"}
end
if status ~= "locked" then
  return {"unavailable"}
end

-- 恢复优惠券为 pending（可再次使用）
redis.call("HSET", coupon_key, "status", "pending", "cancelled_at", now, "locked_at", "")

return {"ok", "0"}
