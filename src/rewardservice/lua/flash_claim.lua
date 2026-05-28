-- rewardservice:flash_claim
-- 原子执行秒杀优惠券抢购：检查池存量 + 防重复 + 扣金币 + 生成优惠券
local pool_key    = KEYS[1]   -- flash:{slot}:remaining
local claimed_key = KEYS[2]   -- flash:{slot}:claimed:{session_id}
local coins_key   = KEYS[3]   -- coins:{session_id}
local coupon_key  = KEYS[4]   -- coupon:{code}

local pool_size       = tonumber(ARGV[1])
local cost_coins      = tonumber(ARGV[2])
local ttl_sec         = tonumber(ARGV[3])
local discount_pct    = ARGV[4]
local session_id      = ARGV[5]
local created_at      = ARGV[6]
local coupon_ttl_sec  = tonumber(ARGV[7])
local coupon_code     = ARGV[8]

-- 防重复领取
if redis.call("EXISTS", claimed_key) == 1 then
  return {"already_claimed"}
end

-- 初始化池（首次请求时原子初始化）
if redis.call("EXISTS", pool_key) == 0 then
  redis.call("SET", pool_key, pool_size, "EX", ttl_sec)
end

-- 原子扣减库存（先 DECR 再检查，防止超卖）
local new_remaining = redis.call("DECR", pool_key)
if new_remaining < 0 then
  redis.call("INCR", pool_key)  -- 回滚
  return {"sold_out"}
end

-- 检查金币是否足够
if cost_coins > 0 then
  local coins = tonumber(redis.call("GET", coins_key) or "0")
  if coins < cost_coins then
    redis.call("INCR", pool_key)  -- 回滚库存
    return {"insufficient_coins", tostring(coins)}
  end
  redis.call("DECRBY", coins_key, cost_coins)
end

-- 标记已领取
redis.call("SET", claimed_key, "1", "EX", ttl_sec)

-- 原子生成优惠券（在 Lua 内完成，防止崩溃丢券）
redis.call("HSET", coupon_key,
  "discount_pct", discount_pct,
  "session_id",   session_id,
  "status",       "pending",
  "created_at",   created_at,
  "source",       "flash",
  "cost_coins",   tostring(cost_coins)
)
redis.call("EXPIRE", coupon_key, coupon_ttl_sec)
return {"ok", tostring(new_remaining), coupon_code}
