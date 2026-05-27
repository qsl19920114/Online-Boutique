-- rewardservice:tree_water
-- 免费浇水：每天1次，增加成长值 + 少量金币，原子操作
local tree_key      = KEYS[1]   -- tree:{session_id}
local water_key     = KEYS[2]   -- tree_water:{session_id}:{date}
local coins_key     = KEYS[3]   -- coins:{session_id}

local today         = ARGV[1]
local growth_gain   = tonumber(ARGV[2])
local coins_gain    = tonumber(ARGV[3])
local day_ttl       = tonumber(ARGV[4])
local growth_target = tonumber(ARGV[5])

-- 今天已浇过水
if redis.call("EXISTS", water_key) == 1 then
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

-- 设置今日浇水标记
redis.call("SET", water_key, "1", "EX", day_ttl)

-- 加少量金币
local balance = redis.call("INCRBY", coins_key, coins_gain)

return {"ok", tostring(growth), tostring(coins_gain), tostring(balance), harvested}
