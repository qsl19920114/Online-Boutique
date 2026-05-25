-- rewardservice:earn
local cooldown_key = KEYS[1]
local idem_key = KEYS[2]
local coins_key = KEYS[3]
local log_key = KEYS[4]

local coins_to_add = tonumber(ARGV[1])
local cooldown_sec = tonumber(ARGV[2])
local idem_ttl_sec = tonumber(ARGV[3])
local log_ttl_sec = tonumber(ARGV[4])
local log_entry = ARGV[5]

if redis.call("EXISTS", cooldown_key) == 1 then
  return {"cooldown", redis.call("TTL", cooldown_key)}
end

if redis.call("EXISTS", idem_key) == 1 then
  return {"duplicate", redis.call("GET", coins_key) or "0"}
end

local balance = redis.call("INCRBY", coins_key, coins_to_add)
redis.call("SET", cooldown_key, "1", "EX", cooldown_sec)
redis.call("SET", idem_key, "1", "EX", idem_ttl_sec)
redis.call("LPUSH", log_key, log_entry)
redis.call("LTRIM", log_key, 0, 49)
redis.call("EXPIRE", log_key, log_ttl_sec)

return {"ok", balance}
