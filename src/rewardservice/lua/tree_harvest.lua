-- rewardservice:tree_harvest
-- 收获成熟树的金币，并自动种下下一级树
local tree_key  = KEYS[1]   -- tree:{session_id}
local coins_key = KEYS[2]   -- coins:{session_id}

local harvest_coins    = tonumber(ARGV[1])
local next_stage       = tonumber(ARGV[2])
local next_growth_target = ARGV[3]  -- 下一级的成长目标（字符串，用于存储）
local now              = ARGV[4]

-- 树必须存在
if redis.call("EXISTS", tree_key) == 0 then
  return {"no_tree"}
end

-- 必须已成熟
if redis.call("HGET", tree_key, "harvested") ~= "1" then
  return {"not_ready"}
end

-- 加收获金币
local balance = redis.call("INCRBY", coins_key, harvest_coins)

-- 重置树为下一级（循环：3级后回到1级）
redis.call("HSET", tree_key,
  "stage",       tostring(next_stage),
  "growth",       "0",
  "water_count",  "0",
  "planted_at",   now,
  "harvested",    "0"
)

return {"ok", tostring(harvest_coins), tostring(balance), tostring(next_stage)}
