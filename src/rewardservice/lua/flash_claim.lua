-- rewardservice:flash_claim
-- 原子执行秒杀优惠券抢购：检查池存量 + 防重复 + 扣金币
local pool_key    = KEYS[1]   -- flash:{slot}:remaining
local claimed_key = KEYS[2]   -- flash:{slot}:claimed:{session_id}
local coins_key   = KEYS[3]   -- coins:{session_id}

local pool_size       = tonumber(ARGV[1])
local cost_coins      = tonumber(ARGV[2])
local ttl_sec         = tonumber(ARGV[3])

-- 防重复领取
if redis.call("EXISTS", claimed_key) == 1 then
  return {"already_claimed"}
end

-- 初始化池（首次请求时原子初始化）
if redis.call("EXISTS", pool_key) == 0 then
  redis.call("SET", pool_key, pool_size, "EX", ttl_sec)
end

local remaining = tonumber(redis.call("GET", pool_key) or "0")
if remaining <= 0 then
  return {"sold_out"}
end

-- 检查金币是否足够
if cost_coins > 0 then
  local coins = tonumber(redis.call("GET", coins_key) or "0")
  if coins < cost_coins then
    return {"insufficient_coins", tostring(coins)}
  end
  redis.call("DECRBY", coins_key, cost_coins)
end

-- 原子扣减库存 + 标记已领取
local new_remaining = redis.call("DECR", pool_key)
redis.call("SET", claimed_key, "1", "EX", ttl_sec)

return {"ok", tostring(new_remaining)}
