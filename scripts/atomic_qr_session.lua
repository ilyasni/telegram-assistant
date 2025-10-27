-- Атомарная смена активной QR попытки
-- KEYS[1] = tg:qr:active:<USER_ID>
-- KEYS[2] = tg:qr:session:<SESSION_ID_NEW>
-- KEYS[3] = tg:qr:sessions_zset:<USER_ID>   -- для сортировки/истории (опц.)

-- ARGV[1] = <SESSION_ID_NEW>
-- ARGV[2] = <now_unix>
-- ARGV[3] = <ttl_seconds>

local old = redis.call('GET', KEYS[1])
if old and old ~= ARGV[1] then
  redis.call('HSET', 'tg:qr:session:'..old, 'status', 'superseded', 'superseded_by', ARGV[1], 'superseded_at', ARGV[2])
end

-- активный указатель
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])

-- инициализация новой попытки (idемпотентно)
redis.call('HSETNX', KEYS[2], 'session_id', ARGV[1])
redis.call('HSET', KEYS[2], 'status', 'pending', 'created_at', ARGV[2])

-- индексировать (опционально)
redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])

return old
