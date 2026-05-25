-- rewardservice:checkin
local today_key = KEYS[1]
local streak_key = KEYS[2]
local coins_key = KEYS[3]

local today = ARGV[1]
local yesterday = ARGV[2]
local today_ttl_sec = tonumber(ARGV[3])
local streak_ttl_sec = tonumber(ARGV[4])
local base_coins = tonumber(ARGV[5])
local weekly_coins = tonumber(ARGV[6])

if redis.call("EXISTS", today_key) == 1 then
  return {"duplicate"}
end

local last_date = redis.call("HGET", streak_key, "last_date")
local streak = tonumber(redis.call("HGET", streak_key, "streak") or "0")
if last_date == yesterday then
  streak = streak + 1
else
  streak = 1
end

local coins = base_coins
if streak % 7 == 0 then
  coins = weekly_coins
end

redis.call("SET", today_key, "1", "EX", today_ttl_sec)
redis.call("HSET", streak_key, "last_date", today, "streak", streak)
redis.call("EXPIRE", streak_key, streak_ttl_sec)
local balance = redis.call("INCRBY", coins_key, coins)

return {"ok", coins, balance, streak}
