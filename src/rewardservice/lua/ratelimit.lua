-- rewardservice:ratelimit
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local expire_sec = tonumber(ARGV[2])

local count = redis.call("INCR", key)
if count == 1 then
  redis.call("EXPIRE", key, expire_sec)
end
if count > limit then
  return 0
end
return 1
