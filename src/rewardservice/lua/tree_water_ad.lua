-- rewardservice:tree_water_ad
-- 看广告浇水：每天最多3次，只加成长值不加金币，原子操作
local tree_key      = KEYS[1]   -- tree:{session_id}
local idem_key      = KEYS[2]   -- tree_ad_water:{session_id}:{date}:{ad_id}
local coins_key     = KEYS[3]   -- coins:{session_id}  (not used for coins, just for key pattern)
local ad_count_key  = KEYS[4]   -- tree_ad_water_count:{session_id}:{date}

local growth_gain    = tonumber(ARGV[1])
local day_ttl        = tonumber(ARGV[2])
local growth_target  = tonumber(ARGV[3])
local max_ad_waters  = tonumber(ARGV[4])

-- 幂等检查
if redis.call("EXISTS", idem_key) == 1 then
  return {"duplicate"}
end

-- 树必须存在
if redis.call("EXISTS", tree_key) == 0 then
  return {"no_tree"}
end

-- 已收获的树不能浇水
if redis.call("HGET", tree_key, "harvested") == "1" then
  return {"already_harvested"}
end

-- 检查今日广告浇水次数
local ad_count = tonumber(redis.call("GET", ad_count_key) or "0")
if ad_count >= max_ad_waters then
  return {"limit_reached"}
end

-- 增加成长值
local growth = tonumber(redis.call("HGET", tree_key, "growth") or "0")
growth = growth + growth_gain

-- 判断是否成熟
local harvested = "0"
if growth >= growth_target then
  growth = growth_target
  harvested = "1"
end

-- 更新树状态
redis.call("HSET", tree_key, "growth", tostring(growth), "harvested", harvested)
redis.call("HINCRBY", tree_key, "water_count", 1)

-- 标记本次广告浇水幂等
redis.call("SET", idem_key, "1", "EX", day_ttl)

-- 递增广告浇水计数
local new_count = redis.call("INCR", ad_count_key)
if new_count == 1 then
  redis.call("EXPIRE", ad_count_key, day_ttl)
end

return {"ok", tostring(growth), tostring(new_count), harvested}
