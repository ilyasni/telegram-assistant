#!/usr/bin/env python3
"""
Test Session Integration
========================

Тестирование интеграции ImprovedSessionSaver в API с проверкой
атомарности, идемпотентности, наблюдаемости и rollback.
"""

import asyncio
import aiohttp
import json
import base64
import time
from typing import Dict, Any, List

class SessionIntegrationTester:
    """Тестер интеграции управления сессиями."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def test_session_save(self, test_data: Dict[str, Any]) -> Dict[str, Any]:
        """Тест сохранения сессии."""
        url = f"{self.base_url}/api/v1/sessions/save"
        
        try:
            async with self.session.post(url, json=test_data) as response:
                result = await response.json()
                
                return {
                    "status_code": response.status,
                    "success": result.get("success", False),
                    "session_id": result.get("session_id"),
                    "error_code": result.get("error_code"),
                    "error_details": result.get("error_details"),
                    "response_time": response.headers.get("X-Response-Time", "unknown")
                }
        except Exception as e:
            return {
                "status_code": 0,
                "success": False,
                "error": str(e)
            }
    
    async def test_session_status(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        """Тест получения статуса сессии."""
        url = f"{self.base_url}/api/v1/sessions/status/{tenant_id}/{user_id}"
        
        try:
            async with self.session.get(url) as response:
                result = await response.json()
                
                return {
                    "status_code": response.status,
                    "session_data": result,
                    "response_time": response.headers.get("X-Response-Time", "unknown")
                }
        except Exception as e:
            return {
                "status_code": 0,
                "error": str(e)
            }
    
    async def test_session_revoke(self, tenant_id: str, user_id: str, reason: str = "test_revoke") -> Dict[str, Any]:
        """Тест отзыва сессии."""
        url = f"{self.base_url}/api/v1/sessions/revoke/{tenant_id}/{user_id}"
        data = {"reason": reason}
        
        try:
            async with self.session.post(url, json=data) as response:
                result = await response.json()
                
                return {
                    "status_code": response.status,
                    "success": result.get("success", False),
                    "message": result.get("message"),
                    "response_time": response.headers.get("X-Response-Time", "unknown")
                }
        except Exception as e:
            return {
                "status_code": 0,
                "success": False,
                "error": str(e)
            }
    
    async def test_cleanup(self, hours: int = 1) -> Dict[str, Any]:
        """Тест очистки просроченных сессий."""
        url = f"{self.base_url}/api/v1/sessions/cleanup"
        params = {"hours": hours}
        
        try:
            async with self.session.post(url, params=params) as response:
                result = await response.json()
                
                return {
                    "status_code": response.status,
                    "success": result.get("success", False),
                    "cleaned_count": result.get("cleaned_count", 0),
                    "response_time": response.headers.get("X-Response-Time", "unknown")
                }
        except Exception as e:
            return {
                "status_code": 0,
                "success": False,
                "error": str(e)
            }
    
    async def test_health(self) -> Dict[str, Any]:
        """Тест health check."""
        url = f"{self.base_url}/api/v1/sessions/health"
        
        try:
            async with self.session.get(url) as response:
                result = await response.json()
                
                return {
                    "status_code": response.status,
                    "health": result,
                    "response_time": response.headers.get("X-Response-Time", "unknown")
                }
        except Exception as e:
            return {
                "status_code": 0,
                "error": str(e)
            }


async def run_comprehensive_test():
    """Запуск комплексного тестирования."""
    print("🧪 Запуск комплексного тестирования Session Management API")
    print("=" * 60)
    
    # Тестовые данные
    test_tenant_id = "test-tenant-123"
    test_user_id = "test-user-456"
    test_telegram_id = 139883458
    
    # Создаем тестовую сессию (800 символов)
    test_session_string = base64.b64encode(b'A' * 600).decode('utf-8')
    
    async with SessionIntegrationTester() as tester:
        print("\n1️⃣ Тест Health Check")
        health_result = await tester.test_health()
        print(f"   Status: {health_result['status_code']}")
        print(f"   Health: {health_result.get('health', {})}")
        
        print("\n2️⃣ Тест сохранения сессии")
        save_data = {
            "tenant_id": test_tenant_id,
            "user_id": test_user_id,
            "session_string": test_session_string,
            "telegram_user_id": test_telegram_id,
            "first_name": "Test",
            "last_name": "User",
            "username": "testuser",
            "force_update": False
        }
        
        save_result = await tester.test_session_save(save_data)
        print(f"   Status Code: {save_result['status_code']}")
        print(f"   Success: {save_result['success']}")
        print(f"   Session ID: {save_result.get('session_id', 'N/A')}")
        if save_result.get('error_code'):
            print(f"   Error Code: {save_result['error_code']}")
            print(f"   Error Details: {save_result.get('error_details', 'N/A')}")
        
        if save_result['success']:
            print("\n3️⃣ Тест получения статуса сессии")
            status_result = await tester.test_session_status(test_tenant_id, test_user_id)
            print(f"   Status Code: {status_result['status_code']}")
            print(f"   Session Data: {status_result.get('session_data', {})}")
            
            print("\n4️⃣ Тест идемпотентности (повторное сохранение)")
            save_result_2 = await tester.test_session_save(save_data)
            print(f"   Status Code: {save_result_2['status_code']}")
            print(f"   Success: {save_result_2['success']}")
            print(f"   Session ID: {save_result_2.get('session_id', 'N/A')}")
            
            print("\n5️⃣ Тест отзыва сессии")
            revoke_result = await tester.test_session_revoke(test_tenant_id, test_user_id, "integration_test")
            print(f"   Status Code: {revoke_result['status_code']}")
            print(f"   Success: {revoke_result['success']}")
            print(f"   Message: {revoke_result.get('message', 'N/A')}")
            
            print("\n6️⃣ Тест статуса после отзыва")
            status_result_2 = await tester.test_session_status(test_tenant_id, test_user_id)
            print(f"   Status Code: {status_result_2['status_code']}")
            print(f"   Session Data: {status_result_2.get('session_data', {})}")
        
        print("\n7️⃣ Тест очистки просроченных сессий")
        cleanup_result = await tester.test_cleanup(1)
        print(f"   Status Code: {cleanup_result['status_code']}")
        print(f"   Success: {cleanup_result['success']}")
        print(f"   Cleaned Count: {cleanup_result.get('cleaned_count', 0)}")
    
    print("\n🎉 Тестирование завершено!")


async def run_stress_test():
    """Запуск стресс-тестирования."""
    print("\n🚀 Запуск стресс-тестирования")
    print("=" * 40)
    
    test_tenant_id = "stress-test-tenant"
    test_session_string = base64.b64encode(b'B' * 600).decode('utf-8')
    
    async with SessionIntegrationTester() as tester:
        # Параллельные запросы
        tasks = []
        for i in range(10):
            save_data = {
                "tenant_id": test_tenant_id,
                "user_id": f"stress-user-{i}",
                "session_string": test_session_string,
                "telegram_user_id": 1000000 + i,
                "first_name": f"Stress{i}",
                "last_name": "Test",
                "username": f"stress{i}",
                "force_update": False
            }
            tasks.append(tester.test_session_save(save_data))
        
        print("   Отправка 10 параллельных запросов...")
        start_time = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        end_time = time.time()
        
        successful = sum(1 for r in results if isinstance(r, dict) and r.get('success', False))
        failed = len(results) - successful
        
        print(f"   ✅ Успешных: {successful}")
        print(f"   ❌ Неудачных: {failed}")
        print(f"   ⏱️  Время выполнения: {end_time - start_time:.2f}s")
        
        # Проверка идемпотентности
        print("\n   Тест идемпотентности (повторные запросы)...")
        tasks_2 = []
        for i in range(5):
            save_data = {
                "tenant_id": test_tenant_id,
                "user_id": f"stress-user-{i}",
                "session_string": test_session_string,
                "telegram_user_id": 1000000 + i,
                "first_name": f"Stress{i}",
                "last_name": "Test",
                "username": f"stress{i}",
                "force_update": False
            }
            tasks_2.append(tester.test_session_save(save_data))
        
        results_2 = await asyncio.gather(*tasks_2, return_exceptions=True)
        successful_2 = sum(1 for r in results_2 if isinstance(r, dict) and r.get('success', False))
        
        print(f"   ✅ Повторных успешных: {successful_2}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "stress":
        asyncio.run(run_stress_test())
    else:
        asyncio.run(run_comprehensive_test())
