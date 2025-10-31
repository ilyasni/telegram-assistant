"""
Supabase client integration for UnifiedSessionManager.

Context7 best practice: интеграция с Supabase для RLS, Storage и Auth.
Использует MCP Supabase для безопасного доступа к данным.
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
import structlog
from supabase import create_client, Client
from supabase.client import SupabaseClient
from supabase.lib.client_options import ClientOptions

from config import settings

logger = structlog.get_logger()


class SupabaseManager:
    """
    Context7: Менеджер для работы с Supabase.
    
    Features:
    - RLS-изолированные операции с telegram_sessions
    - Service role для внутренних операций
    - Storage для бэкапов сессий
    - Auth integration для tenant isolation
    """
    
    def __init__(self):
        self.client: Optional[SupabaseClient] = None
        self.service_client: Optional[SupabaseClient] = None
        self._initialized = False
        
    async def initialize(self) -> bool:
        """
        Инициализация Supabase клиентов.
        
        Returns:
            True если инициализация успешна
        """
        try:
            # Получаем URL и ключи из environment
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
            supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            
            if not all([supabase_url, supabase_anon_key, supabase_service_key]):
                logger.error("Supabase credentials not found in environment")
                return False
            
            # Создаем клиенты с оптимальными настройками
            client_options = ClientOptions(
                auto_refresh_token=True,
                persist_session=True,
                detect_session_in_url=True
            )
            
            self.client = create_client(supabase_url, supabase_anon_key, client_options)
            self.service_client = create_client(supabase_url, supabase_service_key, client_options)
            
            # Проверяем подключение
            await self._test_connection()
            
            self._initialized = True
            logger.info("Supabase client initialized successfully")
            return True
            
        except Exception as e:
            logger.error("Failed to initialize Supabase client", error=str(e))
            return False
    
    async def _test_connection(self):
        """Тестирование подключения к Supabase."""
        try:
            # Простой запрос для проверки подключения
            result = self.service_client.table("telegram_sessions").select("count").execute()
            logger.debug("Supabase connection test successful")
        except Exception as e:
            logger.error("Supabase connection test failed", error=str(e))
            raise
    
    def get_client(self, use_service_role: bool = False) -> SupabaseClient:
        """
        Получение Supabase клиента.
        
        Args:
            use_service_role: Использовать service role (обходит RLS)
            
        Returns:
            SupabaseClient
        """
        if not self._initialized:
            raise RuntimeError("SupabaseManager not initialized")
        
        if use_service_role:
            return self.service_client
        else:
            return self.client
    
    async def create_session(self, session_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Создание записи сессии в telegram_sessions.
        
        Args:
            session_data: Данные сессии
            
        Returns:
            Созданная запись или None
        """
        try:
            client = self.get_client(use_service_role=True)
            
            result = client.table("telegram_sessions").insert(session_data).execute()
            
            if result.data:
                logger.info("Session created", 
                           tenant_id=session_data.get("tenant_id"),
                           app_id=session_data.get("app_id"))
                return result.data[0]
            else:
                logger.error("Failed to create session", error=result)
                return None
                
        except Exception as e:
            logger.error("Failed to create session", error=str(e))
            return None
    
    async def update_session(self, tenant_id: str, app_id: str, 
                           updates: Dict[str, Any]) -> bool:
        """
        Обновление записи сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            updates: Обновления
            
        Returns:
            True если обновление успешно
        """
        try:
            client = self.get_client(use_service_role=True)
            
            result = client.table("telegram_sessions").update(updates).eq(
                "tenant_id", tenant_id
            ).eq("app_id", app_id).execute()
            
            if result.data:
                logger.debug("Session updated", 
                           tenant_id=tenant_id,
                           app_id=app_id)
                return True
            else:
                logger.warning("Session not found for update", 
                             tenant_id=tenant_id,
                             app_id=app_id)
                return False
                
        except Exception as e:
            logger.error("Failed to update session", 
                        tenant_id=tenant_id,
                        app_id=app_id,
                        error=str(e))
            return False
    
    async def get_session(self, tenant_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение записи сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            
        Returns:
            Запись сессии или None
        """
        try:
            client = self.get_client(use_service_role=True)
            
            result = client.table("telegram_sessions").select("*").eq(
                "tenant_id", tenant_id
            ).eq("app_id", app_id).execute()
            
            if result.data:
                return result.data[0]
            else:
                return None
                
        except Exception as e:
            logger.error("Failed to get session", 
                        tenant_id=tenant_id,
                        app_id=app_id,
                        error=str(e))
            return None
    
    async def list_sessions(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Получение списка сессий.
        
        Args:
            tenant_id: ID арендатора (если None, возвращает все)
            
        Returns:
            Список сессий
        """
        try:
            client = self.get_client(use_service_role=True)
            
            query = client.table("telegram_sessions").select("*")
            
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            
            result = query.execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error("Failed to list sessions", 
                        tenant_id=tenant_id,
                        error=str(e))
            return []
    
    async def delete_session(self, tenant_id: str, app_id: str) -> bool:
        """
        Удаление записи сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            
        Returns:
            True если удаление успешно
        """
        try:
            client = self.get_client(use_service_role=True)
            
            result = client.table("telegram_sessions").delete().eq(
                "tenant_id", tenant_id
            ).eq("app_id", app_id).execute()
            
            if result.data:
                logger.info("Session deleted", 
                           tenant_id=tenant_id,
                           app_id=app_id)
                return True
            else:
                logger.warning("Session not found for deletion", 
                             tenant_id=tenant_id,
                             app_id=app_id)
                return False
                
        except Exception as e:
            logger.error("Failed to delete session", 
                        tenant_id=tenant_id,
                        app_id=app_id,
                        error=str(e))
            return False
    
    async def upload_session_backup(self, tenant_id: str, app_id: str, 
                                  file_path: str) -> Optional[str]:
        """
        Загрузка бэкапа сессии в Supabase Storage.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            file_path: Путь к файлу бэкапа
            
        Returns:
            Путь к файлу в Storage или None
        """
        try:
            client = self.get_client(use_service_role=True)
            
            # Читаем файл
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # Создаем путь в Storage
            storage_path = f"sessions/{tenant_id}/{app_id}_{os.path.basename(file_path)}"
            
            # Загружаем в Storage
            result = client.storage.from_("backups").upload(
                storage_path, 
                file_data,
                file_options={'content-type': 'application/octet-stream'}
            )
            
            if result.get('error'):
                logger.error("Failed to upload backup", 
                           path=storage_path,
                           error=result['error'])
                return None
            
            logger.info("Session backup uploaded", 
                       tenant_id=tenant_id,
                       app_id=app_id,
                       storage_path=storage_path)
            
            return storage_path
            
        except Exception as e:
            logger.error("Failed to upload session backup", 
                        tenant_id=tenant_id,
                        app_id=app_id,
                        error=str(e))
            return None
    
    async def download_session_backup(self, storage_path: str, 
                                    output_path: str) -> bool:
        """
        Скачивание бэкапа сессии из Supabase Storage.
        
        Args:
            storage_path: Путь к файлу в Storage
            output_path: Путь для сохранения файла
            
        Returns:
            True если скачивание успешно
        """
        try:
            client = self.get_client(use_service_role=True)
            
            # Скачиваем файл
            result = client.storage.from_("backups").download(storage_path)
            
            if result.get('error'):
                logger.error("Failed to download backup", 
                           path=storage_path,
                           error=result['error'])
                return False
            
            # Создаем каталог если нужно
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Сохраняем файл
            with open(output_path, 'wb') as f:
                f.write(result)
            
            logger.info("Session backup downloaded", 
                       storage_path=storage_path,
                       output_path=output_path)
            
            return True
            
        except Exception as e:
            logger.error("Failed to download session backup", 
                        storage_path=storage_path,
                        error=str(e))
            return False
    
    async def list_backups(self, tenant_id: str, app_id: str) -> List[Dict[str, Any]]:
        """
        Получение списка бэкапов сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            
        Returns:
            Список бэкапов
        """
        try:
            client = self.get_client(use_service_role=True)
            
            # Получаем список файлов в папке
            result = client.storage.from_("backups").list(f"sessions/{tenant_id}")
            
            if result.get('error'):
                logger.error("Failed to list backups", 
                           tenant_id=tenant_id,
                           error=result['error'])
                return []
            
            # Фильтруем по app_id
            backups = []
            prefix = f"{app_id}_"
            
            for item in result:
                if item['name'].startswith(prefix):
                    backups.append({
                        'name': item['name'],
                        'path': f"sessions/{tenant_id}/{item['name']}",
                        'size': item.get('metadata', {}).get('size', 0),
                        'created_at': item['created_at'],
                        'updated_at': item['updated_at']
                    })
            
            # Сортируем по дате создания (новые первыми)
            backups.sort(key=lambda x: x['created_at'], reverse=True)
            
            return backups
            
        except Exception as e:
            logger.error("Failed to list backups", 
                        tenant_id=tenant_id,
                        app_id=app_id,
                        error=str(e))
            return []
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для Supabase."""
        try:
            if not self._initialized:
                return {
                    "status": "unhealthy",
                    "reason": "Not initialized"
                }
            
            # Проверяем подключение
            await self._test_connection()
            
            return {
                "status": "healthy",
                "initialized": True,
                "client_available": self.client is not None,
                "service_client_available": self.service_client is not None
            }
            
        except Exception as e:
            logger.error("Supabase health check failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e)
            }


# Context7: Глобальный экземпляр для использования в приложении
supabase_manager = SupabaseManager()
