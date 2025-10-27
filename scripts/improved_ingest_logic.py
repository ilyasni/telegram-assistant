#!/usr/bin/env python3
"""
Improved Telethon Ingest Logic
==============================

Улучшенная логика для telethon-ingest с правильной обработкой QR сессий.
Решает проблему "Skipping failed session without session_string".
"""

import time
import redis
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class ImprovedQRProcessor:
    """Улучшенный процессор QR сессий"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    def get_active_session_for_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Получает ТОЛЬКО активную сессию пользователя"""
        active_key = f"tg:qr:active:{user_id}"
        session_id = self.redis.get(active_key)
        
        if not session_id:
            logger.debug(f"No active session for user {user_id}")
            return None
        
        session_id = session_id.decode()
        session_key = f"tg:qr:session:{session_id}"
        session_data = self.redis.hgetall(session_key)
        
        if not session_data:
            logger.warning(f"Active session {session_id} not found in Redis")
            return None
        
        # Конвертируем bytes в строки
        session = {k.decode(): v.decode() for k, v in session_data.items()}
        session['session_id'] = session_id
        return session
    
    def process_qr_session(self, user_id: str) -> bool:
        """Обрабатывает QR сессию с правильной логикой"""
        session = self.get_active_session_for_user(user_id)
        
        if not session:
            logger.debug(f"No active session for user {user_id}")
            return False
        
        session_id = session['session_id']
        status = session.get('status', 'pending')
        
        # Игнорируем терминальные статусы
        if status in ['failed', 'expired', 'superseded', 'done']:
            logger.debug(f"Session {session_id} has terminal status: {status}")
            return False
        
        # Обрабатываем только активную сессию
        logger.info(f"Processing active session {session_id} with status: {status}")
        
        try:
            # Здесь ваша логика обработки QR
            if status == 'pending':
                self._handle_pending_session(session)
            elif status == 'qr_rendered':
                self._handle_qr_rendered_session(session)
            elif status == 'scanned':
                self._handle_scanned_session(session)
            elif status == 'authorized':
                self._handle_authorized_session(session)
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing session {session_id}: {e}")
            self._mark_session_failed(session_id, str(e))
            return False
    
    def _handle_pending_session(self, session: Dict[str, Any]):
        """Обработка сессии в статусе pending"""
        logger.info(f"Handling pending session: {session['session_id']}")
        # Ваша логика для pending статуса
    
    def _handle_qr_rendered_session(self, session: Dict[str, Any]):
        """Обработка сессии с отрендеренным QR"""
        logger.info(f"Handling QR rendered session: {session['session_id']}")
        # Ваша логика для QR rendered статуса
    
    def _handle_scanned_session(self, session: Dict[str, Any]):
        """Обработка отсканированной сессии"""
        logger.info(f"Handling scanned session: {session['session_id']}")
        # Ваша логика для scanned статуса
    
    def _handle_authorized_session(self, session: Dict[str, Any]):
        """Обработка авторизованной сессии"""
        logger.info(f"Handling authorized session: {session['session_id']}")
        # Ваша логика для authorized статуса
    
    def _mark_session_failed(self, session_id: str, error: str):
        """Помечает сессию как failed с детальной информацией"""
        session_key = f"tg:qr:session:{session_id}"
        now = int(time.time())
        
        self.redis.hset(session_key, mapping={
            'status': 'failed',
            'last_error': error,
            'failed_at': str(now),
            'updated_at': str(now)
        })
        
        logger.error(f"Session {session_id} marked as failed: {error}")
    
    def scan_all_active_sessions(self) -> int:
        """Сканирует все активные сессии"""
        # Найти всех пользователей с активными сессиями
        active_keys = self.redis.keys("tg:qr:active:*")
        processed = 0
        
        for key in active_keys:
            user_id = key.decode().split(':')[-1]
            if self.process_qr_session(user_id):
                processed += 1
        
        return processed

# Пример использования
if __name__ == "__main__":
    import redis
    
    # Подключение к Redis
    r = redis.Redis(host='localhost', port=6379, db=0)
    processor = ImprovedQRProcessor(r)
    
    # Обработка сессии для конкретного пользователя
    user_id = "139883458"
    success = processor.process_qr_session(user_id)
    print(f"Processing result for user {user_id}: {success}")
    
    # Сканирование всех активных сессий
    processed = processor.scan_all_active_sessions()
    print(f"Processed {processed} active sessions")
