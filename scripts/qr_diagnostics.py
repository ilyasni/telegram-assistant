#!/usr/bin/env python3
"""
QR Session Diagnostics
======================

Диагностика и мониторинг QR сессий для выявления проблем.
"""

import redis
import json
import time
from typing import Dict, List, Any

class QRDiagnostics:
    """Диагностика QR сессий"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    def get_user_sessions(self, user_id: str) -> Dict[str, Any]:
        """Получает все сессии пользователя"""
        # Активная сессия
        active_key = f"tg:qr:active:{user_id}"
        active_session_id = self.redis.get(active_key)
        
        # Все сессии пользователя
        session_keys = self.redis.keys(f"tg:qr:session:*{user_id}*")
        sessions = []
        
        for key in session_keys:
            session_data = self.redis.hgetall(key)
            if session_data:
                session = {k.decode(): v.decode() for k, v in session_data.items()}
                session['key'] = key.decode()
                sessions.append(session)
        
        # История сессий
        zset_key = f"tg:qr:sessions_zset:{user_id}"
        history = self.redis.zrevrange(zset_key, 0, 10)
        
        return {
            'user_id': user_id,
            'active_session_id': active_session_id.decode() if active_session_id else None,
            'sessions': sessions,
            'history': [h.decode() for h in history],
            'total_sessions': len(sessions)
        }
    
    def check_session_health(self, user_id: str) -> Dict[str, Any]:
        """Проверяет здоровье сессий пользователя"""
        data = self.get_user_sessions(user_id)
        
        issues = []
        warnings = []
        
        # Проверка активной сессии
        if not data['active_session_id']:
            issues.append("No active session")
        else:
            active_session = next(
                (s for s in data['sessions'] if s.get('session_id') == data['active_session_id']), 
                None
            )
            
            if not active_session:
                issues.append("Active session not found in sessions")
            elif active_session.get('status') in ['failed', 'expired', 'superseded']:
                issues.append(f"Active session has terminal status: {active_session.get('status')}")
        
        # Проверка множественных активных сессий
        active_sessions = [s for s in data['sessions'] if s.get('status') not in ['failed', 'expired', 'superseded', 'done']]
        if len(active_sessions) > 1:
            warnings.append(f"Multiple active sessions: {len(active_sessions)}")
        
        # Проверка старых failed сессий
        failed_sessions = [s for s in data['sessions'] if s.get('status') == 'failed']
        if len(failed_sessions) > 5:
            warnings.append(f"Too many failed sessions: {len(failed_sessions)}")
        
        # Проверка TTL
        for session in data['sessions']:
            created_at = session.get('created_at')
            if created_at:
                age = int(time.time()) - int(created_at)
                if age > 900:  # 15 минут
                    warnings.append(f"Old session {session.get('session_id')}: {age}s old")
        
        return {
            'user_id': user_id,
            'issues': issues,
            'warnings': warnings,
            'health_score': max(0, 100 - len(issues) * 20 - len(warnings) * 5),
            'data': data
        }
    
    def cleanup_user_sessions(self, user_id: str, dry_run: bool = True) -> Dict[str, Any]:
        """Очищает сессии пользователя"""
        data = self.get_user_sessions(user_id)
        
        to_clean = []
        for session in data['sessions']:
            status = session.get('status')
            created_at = session.get('created_at')
            
            # Удалить терминальные сессии старше 1 часа
            if status in ['failed', 'expired', 'superseded']:
                if created_at:
                    age = int(time.time()) - int(created_at)
                    if age > 3600:  # 1 час
                        to_clean.append(session['key'])
            
            # Удалить очень старые сессии
            if created_at:
                age = int(time.time()) - int(created_at)
                if age > 7200:  # 2 часа
                    to_clean.append(session['key'])
        
        if not dry_run:
            for key in to_clean:
                self.redis.delete(key)
        
        return {
            'user_id': user_id,
            'sessions_to_clean': len(to_clean),
            'keys_to_clean': to_clean,
            'dry_run': dry_run
        }
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Получает статистику системы"""
        # Все активные сессии
        active_keys = self.redis.keys("tg:qr:active:*")
        
        # Все сессии
        session_keys = self.redis.keys("tg:qr:session:*")
        
        # Статистика по статусам
        status_counts = {}
        for key in session_keys:
            session_data = self.redis.hgetall(key)
            if session_data:
                status = session_data.get(b'status', b'unknown').decode()
                status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            'total_active_sessions': len(active_keys),
            'total_sessions': len(session_keys),
            'status_distribution': status_counts,
            'timestamp': int(time.time())
        }

# CLI для диагностики
if __name__ == "__main__":
    import sys
    
    r = redis.Redis(host='localhost', port=6379, db=0)
    diag = QRDiagnostics(r)
    
    if len(sys.argv) > 1:
        user_id = sys.argv[1]
        
        print(f"=== QR Sessions for User {user_id} ===")
        data = diag.get_user_sessions(user_id)
        print(json.dumps(data, indent=2))
        
        print(f"\n=== Health Check ===")
        health = diag.check_session_health(user_id)
        print(json.dumps(health, indent=2))
        
        print(f"\n=== Cleanup Preview ===")
        cleanup = diag.cleanup_user_sessions(user_id, dry_run=True)
        print(json.dumps(cleanup, indent=2))
        
    else:
        print("=== System Stats ===")
        stats = diag.get_system_stats()
        print(json.dumps(stats, indent=2))
