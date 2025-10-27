-- Lua script for sliding window rate limiting
-- [C7-ID: API-RATELIMIT-001]

local key = KEYS[1]
local window_size = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local current_time = tonumber(ARGV[3])

-- Remove expired entries
redis.call('ZREMRANGEBYSCORE', key, 0, current_time - window_size)

-- Count current entries
local current_count = redis.call('ZCARD', key)

if current_count < limit then
    -- Add new entry
    redis.call('ZADD', key, current_time, current_time)
    redis.call('EXPIRE', key, window_size)
    return {1, limit - current_count - 1, window_size}
else
    -- Rate limit exceeded
    local oldest_entry = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_time = 0
    if #oldest_entry > 0 then
        reset_time = tonumber(oldest_entry[2]) + window_size
    end
    return {0, 0, reset_time}
end