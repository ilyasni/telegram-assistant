#!/usr/bin/env python3
"""
Improved Session Save Logic
===========================

Улучшенная логика сохранения Telethon сессий с правильной обработкой ошибок.
Решает проблему database_save_failed через правильную архитектуру.
"""

import os
import base64
import logging
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

class ImprovedSessionSaver:
    """Улучшенный сохранение сессий с детальной диагностикой"""
    
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url.replace('+asyncpg', ''), pool_pre_ping=True)
    
    def save_telethon_session(
        self, 
        user_id: str, 
        tenant_id: str,
        session_string: str, 
        key_id: str,
        status: str = 'authorized'
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Сохраняет Telethon сессию с детальной диагностикой ошибок
        
        Returns:
            (success, error_code, error_details)
        """
        try:
            # Логируем длину сессии для диагностики
            logger.info(f"Saving session for user {user_id}, length: {len(session_string)}")
            
            with self.engine.begin() as conn:
                # Используем нашу функцию upsert
                result = conn.execute(text('''
                    SELECT upsert_telegram_session(
                        :tenant_id, :user_id, :session, :key_id, :status, :auth_error, :error_details
                    )
                '''), {
                    'tenant_id': tenant_id,
                    'user_id': user_id,
                    'session': session_string,
                    'key_id': key_id,
                    'status': status,
                    'auth_error': None,
                    'error_details': None
                })
                
                session_id = result.scalar()
                logger.info(f"Session saved successfully, id: {session_id}")
                
                return True, None, None
                
        except SQLAlchemyError as e:
            error_code = self._classify_error(e)
            error_details = str(e)
            
            logger.error(f"Database error saving session: {error_code} - {error_details}")
            return False, error_code, error_details
            
        except Exception as e:
            error_code = "unexpected_error"
            error_details = str(e)
            
            logger.error(f"Unexpected error saving session: {error_details}")
            return False, error_code, error_details
    
    def _classify_error(self, error: SQLAlchemyError) -> str:
        """Классифицирует ошибки базы данных"""
        error_str = str(error).lower()
        
        if 'value too long' in error_str or 'character varying' in error_str:
            return "session_too_long"
        elif 'column does not exist' in error_str:
            return "missing_column"
        elif 'unique constraint' in error_str or 'duplicate key' in error_str:
            return "duplicate_session"
        elif 'foreign key constraint' in error_str:
            return "invalid_tenant_or_user"
        elif 'not null constraint' in error_str:
            return "missing_required_field"
        elif 'permission denied' in error_str or 'insufficient privilege' in error_str:
            return "permission_denied"
        elif 'connection' in error_str or 'timeout' in error_str:
            return "connection_error"
        else:
            return "database_error"
    
    def test_session_save(self, user_id: str = "139883458") -> Dict[str, Any]:
        """Тестирует сохранение сессии с разными длинами"""
        import base64
        
        test_results = {}
        
        # Тест 1: Короткая сессия (100 символов)
        short_session = base64.b64encode(b'A' * 50).decode('utf-8')
        success, error_code, error_details = self.save_telethon_session(
            user_id, "test-tenant", short_session, "test-key-short", "pending"
        )
        test_results['short_session'] = {
            'success': success,
            'error_code': error_code,
            'error_details': error_details,
            'length': len(short_session)
        }
        
        # Тест 2: Средняя сессия (400 символов)
        medium_session = base64.b64encode(b'A' * 200).decode('utf-8')
        success, error_code, error_details = self.save_telethon_session(
            user_id, "test-tenant", medium_session, "test-key-medium", "pending"
        )
        test_results['medium_session'] = {
            'success': success,
            'error_code': error_code,
            'error_details': error_details,
            'length': len(medium_session)
        }
        
        # Тест 3: Длинная сессия (800 символов)
        long_session = base64.b64encode(b'A' * 600).decode('utf-8')
        success, error_code, error_details = self.save_telethon_session(
            user_id, "test-tenant", long_session, "test-key-long", "pending"
        )
        test_results['long_session'] = {
            'success': success,
            'error_code': error_code,
            'error_details': error_details,
            'length': len(long_session)
        }
        
        return test_results

# Пример использования
if __name__ == "__main__":
    import sys
    
    # Подключение к БД
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+asyncpg://telegram_user:telegram_password@localhost:5432/postgres')
    saver = ImprovedSessionSaver(DATABASE_URL)
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Тестирование
        print("🧪 Тестирование сохранения сессий...")
        results = saver.test_session_save()
        
        for test_name, result in results.items():
            status = "✅" if result['success'] else "❌"
            print(f"{status} {test_name}: {result['length']} символов - {result['error_code'] or 'OK'}")
    else:
        # Обычное использование
        user_id = "139883458"
        tenant_id = "test-tenant"
        session_string = base64.b64encode(b'A' * 600).decode('utf-8')
        key_id = "test-key-123"
        
        success, error_code, error_details = saver.save_telethon_session(
            user_id, tenant_id, session_string, key_id, "authorized"
        )
        
        if success:
            print("✅ Сессия сохранена успешно")
        else:
            print(f"❌ Ошибка сохранения: {error_code} - {error_details}")
