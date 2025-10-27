#!/usr/bin/env python3
"""
QR Session Manager
==================

Управление QR сессиями с атомарными операциями и state-машиной.
Решает проблему "database_save_failed" через правильную архитектуру сессий.
"""

import time
import uuid
import redis
import json
from typing import Optional, Dict, Any
from enum import Enum

class QRStatus(Enum):
    """Статусы QR сессий с конечным автоматом"""
    PENDING = "pending"
    QR_RENDERED = "qr_rendered"
    SCANNED = "scanned"
    AUTHORIZED = "authorized"
    SESSION_SAVED = "session_saved"
    DONE = "done"
    
    # Терминальные статусы
    EXPIRED = "expired"
    FAILED = "failed"
    SUPERSEDED = "superseded"

class QRSessionManager:
    """Менеджер QR сессий с атомарными операциями"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.atomic_script = self._load_lua_script()
        self.cleanup_script = self._load_cleanup_script()
    
    def _load_lua_script(self) -> str:
        """Загружает Lua скрипт для атомарной смены сессии"""
        return """
        local old = redis.call('GET', KEYS[1])
        if old and old ~= ARGV[1] then
          redis.call('HSET', 'tg:qr:session:'..old, 'status', 'superseded', 'superseded_by', ARGV[1], 'superseded_at', ARGV[2])
        end
        
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
        redis.call('HSETNX', KEYS[2], 'session_id', ARGV[1])
        redis.call('HSET', KEYS[2], 'status', 'pending', 'created_at', ARGV[2])
        redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
        
        return old
        """
    
    def _load_cleanup_script(self) -> str:
        """Загружает Lua скрипт для очистки мусора"""
        return """
        local user_id = ARGV[1]
        local now = tonumber(ARGV[2])
        local max_age = tonumber(ARGV[3]) or 900
        
        local keys = redis.call('KEYS', 'tg:qr:session:*' .. user_id .. '*')
        local cleaned = 0
        
        for _, key in ipairs(keys) do
          local status = redis.call('HGET', key, 'status')
          local created_at = redis.call('HGET', key, 'created_at')
          
          if status == 'failed' or status == 'expired' or status == 'superseded' then
            redis.call('DEL', key)
            cleaned = cleaned + 1
          elseif created_at and (now - tonumber(created_at)) > max_age then
            redis.call('DEL', key)
            cleaned = cleaned + 1
          end
        end
        
        return cleaned
        """
    
    def create_session(self, user_id: str, ttl_seconds: int = 900) -> str:
        """Создает новую QR сессию атомарно"""
        now = int(time.time())
        session_id = f"{user_id}:{now}:{uuid.uuid4().hex[:8]}"
        
        keys = [
            f"tg:qr:active:{user_id}",
            f"tg:qr:session:{session_id}",
            f"tg:qr:sessions_zset:{user_id}"
        ]
        
        args = [session_id, str(now), str(ttl_seconds)]
        
        # Атомарная смена активной сессии
        old_session = self.redis.eval(self.atomic_script, len(keys), *keys, *args)
        
        return session_id
    
    def get_active_session(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Получает активную сессию пользователя"""
        active_key = f"tg:qr:active:{user_id}"
        session_id = self.redis.get(active_key)
        
        if not session_id:
            return None
        
        session_key = f"tg:qr:session:{session_id.decode()}"
        session_data = self.redis.hgetall(session_key)
        
        if not session_data:
            return None
        
        # Конвертируем bytes в строки
        return {k.decode(): v.decode() for k, v in session_data.items()}
    
    def update_session_status(self, session_id: str, status: QRStatus, 
                            reason: str = None, details: str = None) -> bool:
        """Обновляет статус сессии с детальной информацией об ошибках"""
        session_key = f"tg:qr:session:{session_id}"
        
        update_data = {
            'status': status.value,
            'updated_at': str(int(time.time()))
        }
        
        if reason:
            update_data['last_error'] = reason
        if details:
            update_data['error_details'] = details
        
        return self.redis.hset(session_key, mapping=update_data)
    
    def cleanup_user_sessions(self, user_id: str, max_age_seconds: int = 900) -> int:
        """Очищает просроченные и терминальные сессии пользователя"""
        now = int(time.time())
        return self.redis.eval(self.cleanup_script, 0, user_id, str(now), str(max_age_seconds))
    
    def get_session_history(self, user_id: str, limit: int = 10) -> list:
        """Получает историю сессий пользователя"""
        zset_key = f"tg:qr:sessions_zset:{user_id}"
        sessions = self.redis.zrevrange(zset_key, 0, limit - 1)
        
        history = []
        for session_id in sessions:
            session_key = f"tg:qr:session:{session_id.decode()}"
            session_data = self.redis.hgetall(session_key)
            if session_data:
                history.append({k.decode(): v.decode() for k, v in session_data.items()})
        
        return history

# Пример использования
if __name__ == "__main__":
    import redis
    
    # Подключение к Redis
    r = redis.Redis(host='localhost', port=6379, db=0)
    manager = QRSessionManager(r)
    
    # Создание новой сессии
    user_id = "139883458"
    session_id = manager.create_session(user_id)
    print(f"Created session: {session_id}")
    
    # Получение активной сессии
    active_session = manager.get_active_session(user_id)
    print(f"Active session: {active_session}")
    
    # Обновление статуса
    manager.update_session_status(session_id, QRStatus.QR_RENDERED)
    print("Status updated to QR_RENDERED")
    
    # Очистка старых сессий
    cleaned = manager.cleanup_user_sessions(user_id)
    print(f"Cleaned {cleaned} old sessions")
