-- Сборщик мусора для QR сессий
-- Удаляет просроченные и терминальные сессии

local user_id = ARGV[1]
local now = tonumber(ARGV[2])
local max_age = tonumber(ARGV[3]) or 900  -- 15 минут по умолчанию

-- Найти все сессии пользователя
local keys = redis.call('KEYS', 'tg:qr:session:*' .. user_id .. '*')
local cleaned = 0

for _, key in ipairs(keys) do
  local status = redis.call('HGET', key, 'status')
  local created_at = redis.call('HGET', key, 'created_at')
  
  -- Удалить терминальные сессии
  if status == 'failed' or status == 'expired' or status == 'superseded' then
    redis.call('DEL', key)
    cleaned = cleaned + 1
  -- Удалить просроченные сессии
  elseif created_at and (now - tonumber(created_at)) > max_age then
    redis.call('DEL', key)
    cleaned = cleaned + 1
  end
end

-- Очистить ZSET от удаленных сессий
local zset_key = 'tg:qr:sessions_zset:' .. user_id
local sessions = redis.call('ZRANGE', zset_key, 0, -1)
for _, session_id in ipairs(sessions) do
  local session_key = 'tg:qr:session:' .. session_id
  if redis.call('EXISTS', session_key) == 0 then
    redis.call('ZREM', zset_key, session_id)
  end
end

return cleaned
