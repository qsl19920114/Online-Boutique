-- rewardservice:rush_claim
-- 原子执行整点抢金币：检查池存量 + 防重复 + 发放金币
local pool_key    = KEYS[1]   -- rush:{slot}:remaining
local claimed_key = KEYS[2]   -- rush:{slot}:claimed:{session_id}
local coins_key   = KEYS[3]   -- coins:{session_id}

local pool_size       = tonumber(ARGV[1])
local coins_per_claim = tonumber(ARGV[2])
local ttl_sec         = tonumber(ARGV[3])

-- 防重复领取
if redis.call("EXISTS", claimed_key) == 1 then
  return {"already_claimed"}
end

-- 原子初始化池（首次请求时设置库存）
local remaining = redis.call("GET", pool_key)
if not remaining then
  redis.call("SET", pool_key, tostring(pool_size - 1), "EX", ttl_sec)
  redis.call("SET", claimed_key, "1", "EX", ttl_sec)
  local balance = redis.call("INCRBY", coins_key, coins_per_claim)
  return {"ok", tostring(coins_per_claim), tostring(balance), tostring(pool_size - 1)}
end

-- 原子扣减库存（先 DECR 再检查，防止超卖）
local new_remaining = redis.call("DECR", pool_key)
if new_remaining < 0 then
  redis.call("INCR", pool_key)  -- 回滚
  return {"sold_out"}
end

-- 发放金币 + 标记已领取
redis.call("SET", claimed_key, "1", "EX", ttl_sec)
local balance = redis.call("INCRBY", coins_key, coins_per_claim)

return {"ok", tostring(coins_per_claim), tostring(balance), tostring(new_remaining)}
