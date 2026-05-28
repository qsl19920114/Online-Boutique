-- rewardservice:watch_event
-- Atomically update max watch progress and aggregate event state.
local watch_key = KEYS[1]

local position_ms = tonumber(ARGV[1])
local event = ARGV[2]
local ttl_sec = tonumber(ARGV[3])

local current = tonumber(redis.call("HGET", watch_key, "max_position_ms") or "0")
if position_ms > current then
  current = position_ms
  redis.call("HSET", watch_key, "max_position_ms", tostring(current))
end

redis.call("HSET", watch_key, "last_event", event)
if event == "waiting" then
  redis.call("HINCRBY", watch_key, "rebuffer_count", 1)
end
if event == "error" then
  redis.call("HINCRBY", watch_key, "error_count", 1)
end
if event == "ended" then
  redis.call("HSET", watch_key, "ended", "1")
end
redis.call("EXPIRE", watch_key, ttl_sec)

return {tostring(current)}
