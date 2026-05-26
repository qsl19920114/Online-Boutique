-- rewardservice:coupon_cancel
-- 取消锁定优惠券并退还金币（仅限 locked 状态）
local coupon_key = KEYS[1]
local coins_key  = KEYS[2]

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

-- 读取优惠券面额（扣除的金币数）
local discount_pct = tonumber(redis.call("HGET", coupon_key, "discount_pct"))
local source = redis.call("HGET", coupon_key, "source")

-- 根据来源计算退还金币数
local refund = 0
if source == "flash" then
  -- 秒杀优惠券：退还 flash_cost_coins 字段
  local cost_str = redis.call("HGET", coupon_key, "cost_coins")
  refund = tonumber(cost_str or "0")
else
  -- 普通兑换：按 discount_pct 反查 COUPON_RULES
  if discount_pct == 90 then
    refund = 50
  elseif discount_pct == 80 then
    refund = 100
  end
end

-- 退还金币
if refund > 0 then
  redis.call("INCRBY", coins_key, refund)
end

-- 恢复优惠券为 pending（可再次使用）
redis.call("HSET", coupon_key, "status", "pending", "cancelled_at", now)

return {"ok", tostring(refund)}
