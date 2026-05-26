-- rewardservice:ratelimit
-- 滑动窗口限流：基于 Sorted Set 实现精确滑动窗口
-- 每个请求以 timestamp 为 score 插入，清理过期成员后计数
local key = KEYS[1]
local limit      = tonumber(ARGV[1])
local window_sec = tonumber(ARGV[2])
local now_ms     = tonumber(ARGV[3])

local window_start = now_ms - window_sec * 1000

-- 清理窗口外的旧记录
redis.call("ZREMRANGEBYSCORE", key, "-inf", window_start)

-- 当前窗口内的请求数
local count = redis.call("ZCARD", key)
if count >= limit then
  return 0
end

-- 插入当前请求
redis.call("ZADD", key, now_ms, now_ms .. ":" .. math.random(1, 1000000))
redis.call("EXPIRE", key, window_sec + 1)

return 1
