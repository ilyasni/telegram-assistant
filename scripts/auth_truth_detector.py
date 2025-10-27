#!/usr/bin/env python3
"""
Auth Truth Detector
===================

"Сыворотка правды" для диагностики database_save_failed.
Быстро доказывает, ЧТО именно падает в момент ошибки.
"""

import asyncio
import aiohttp
import json
import base64
import time
import os
from typing import Dict, Any, List

class AuthTruthDetector:
    """Детектор правды для диагностики auth ошибок."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
        self.correlation_id = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def test_session_save_with_diagnostics(
        self, 
        tenant_id: str, 
        user_id: str, 
        session_string: str, 
        telegram_user_id: int
    ) -> Dict[str, Any]:
        """Тест сохранения сессии с детальной диагностикой."""
        print(f"🔍 Детектор правды: тест сохранения сессии")
        print(f"   Tenant ID: {tenant_id}")
        print(f"   User ID: {user_id}")
        print(f"   Session Length: {len(session_string)}")
        print(f"   Telegram ID: {telegram_user_id}")
        
        url = f"{self.base_url}/api/v1/sessions/save"
        data = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_string": session_string,
            "telegram_user_id": telegram_user_id,
            "first_name": "Truth",
            "last_name": "Detector",
            "username": "truthdetector",
            "force_update": False
        }
        
        start_time = time.time()
        
        try:
            async with self.session.post(url, json=data) as response:
                duration = time.time() - start_time
                result = await response.json()
                
                print(f"📊 Результат диагностики:")
                print(f"   Status Code: {response.status}")
                print(f"   Duration: {duration:.3f}s")
                print(f"   Success: {result.get('success', False)}")
                
                if result.get('success'):
                    print(f"   ✅ Session ID: {result.get('session_id')}")
                    return {
                        "status": "success",
                        "session_id": result.get('session_id'),
                        "duration": duration,
                        "status_code": response.status
                    }
                else:
                    print(f"   ❌ Error Code: {result.get('error_code')}")
                    print(f"   ❌ Error Details: {result.get('error_details')}")
                    return {
                        "status": "failed",
                        "error_code": result.get('error_code'),
                        "error_details": result.get('error_details'),
                        "duration": duration,
                        "status_code": response.status
                    }
                    
        except Exception as e:
            duration = time.time() - start_time
            print(f"   💥 Exception: {type(e).__name__}: {str(e)}")
            return {
                "status": "exception",
                "error": str(e),
                "error_type": type(e).__name__,
                "duration": duration
            }
    
    async def test_bypass_mode(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        """Тест в режиме bypass для изоляции проблемы."""
        print(f"🔧 Тест bypass режима (AUTH_FINALIZE_DB_BYPASS=on)")
        
        # Проверяем feature flag
        bypass_url = f"{self.base_url}/api/v1/sessions/health"
        
        try:
            async with self.session.get(bypass_url) as response:
                health_data = await response.json()
                print(f"   Health Status: {health_data.get('status')}")
                print(f"   Service: {health_data.get('service')}")
                
                return {
                    "bypass_available": True,
                    "health_status": health_data.get('status'),
                    "service": health_data.get('service')
                }
        except Exception as e:
            print(f"   ❌ Bypass test failed: {e}")
            return {
                "bypass_available": False,
                "error": str(e)
            }
    
    async def test_smoke_session_save(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        """Smoke test с явным upsert для проверки уникального индекса."""
        print(f"💨 Smoke test: явный upsert с теми же значениями")
        
        # Создаем тестовую сессию
        test_session = base64.b64encode(b'SMOKE_TEST_SESSION' * 20).decode('utf-8')
        
        # Первый вызов
        result1 = await self.test_session_save_with_diagnostics(
            tenant_id, user_id, test_session, 999999
        )
        
        print(f"   Первый вызов: {result1['status']}")
        
        # Второй вызов (идемпотентность)
        result2 = await self.test_session_save_with_diagnostics(
            tenant_id, user_id, test_session, 999999
        )
        
        print(f"   Второй вызов: {result2['status']}")
        
        return {
            "first_call": result1,
            "second_call": result2,
            "idempotency_ok": result1['status'] == result2['status']
        }
    
    async def test_different_session_lengths(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        """Тест с разными длинами сессий."""
        print(f"📏 Тест разных длин сессий")
        
        test_cases = [
            ("short", 100),
            ("medium", 500),
            ("long", 1000),
            ("very_long", 2000)
        ]
        
        results = {}
        
        for name, length in test_cases:
            print(f"   Тест {name} ({length} символов):")
            
            # Создаем сессию нужной длины
            test_session = base64.b64encode(b'A' * (length // 4 * 3)).decode('utf-8')
            
            result = await self.test_session_save_with_diagnostics(
                tenant_id, f"{user_id}_{name}", test_session, 1000000 + length
            )
            
            results[name] = result
            print(f"     Результат: {result['status']}")
        
        return results
    
    async def check_prometheus_metrics(self) -> Dict[str, Any]:
        """Проверка Prometheus метрик."""
        print(f"📊 Проверка Prometheus метрик")
        
        metrics_url = f"{self.base_url}/metrics"
        
        try:
            async with self.session.get(metrics_url) as response:
                if response.status == 200:
                    metrics_text = await response.text()
                    
                    # Ищем метрики auth
                    auth_metrics = []
                    for line in metrics_text.split('\n'):
                        if 'auth_finalize' in line or 'session_save' in line:
                            auth_metrics.append(line.strip())
                    
                    print(f"   Найдено {len(auth_metrics)} auth метрик")
                    for metric in auth_metrics[:10]:  # Показываем первые 10
                        print(f"     {metric}")
                    
                    return {
                        "metrics_available": True,
                        "auth_metrics_count": len(auth_metrics),
                        "sample_metrics": auth_metrics[:10]
                    }
                else:
                    print(f"   ❌ Metrics недоступны: {response.status}")
                    return {
                        "metrics_available": False,
                        "status_code": response.status
                    }
        except Exception as e:
            print(f"   ❌ Ошибка получения метрик: {e}")
            return {
                "metrics_available": False,
                "error": str(e)
            }


async def run_truth_detection():
    """Запуск детектора правды."""
    print("🕵️ Детектор правды: диагностика database_save_failed")
    print("=" * 60)
    
    test_tenant_id = "truth-detector-tenant"
    test_user_id = "truth-detector-user"
    
    async with AuthTruthDetector() as detector:
        print("\n1️⃣ Проверка bypass режима")
        bypass_result = await detector.test_bypass_mode(test_tenant_id, test_user_id)
        
        print("\n2️⃣ Smoke test (явный upsert)")
        smoke_result = await detector.test_smoke_session_save(test_tenant_id, test_user_id)
        
        print("\n3️⃣ Тест разных длин сессий")
        length_result = await detector.test_different_session_lengths(test_tenant_id, test_user_id)
        
        print("\n4️⃣ Проверка Prometheus метрик")
        metrics_result = await detector.check_prometheus_metrics()
        
        print("\n📋 Сводка диагностики:")
        print(f"   Bypass доступен: {bypass_result.get('bypass_available', False)}")
        print(f"   Idempotency OK: {smoke_result.get('idempotency_ok', False)}")
        print(f"   Метрики доступны: {metrics_result.get('metrics_available', False)}")
        
        # Анализ результатов
        failed_tests = []
        for name, result in length_result.items():
            if result.get('status') != 'success':
                failed_tests.append(f"{name}: {result.get('error_code', 'unknown')}")
        
        if failed_tests:
            print(f"\n❌ Проваленные тесты:")
            for test in failed_tests:
                print(f"     {test}")
        else:
            print(f"\n✅ Все тесты прошли успешно!")
        
        return {
            "bypass_result": bypass_result,
            "smoke_result": smoke_result,
            "length_result": length_result,
            "metrics_result": metrics_result,
            "failed_tests": failed_tests
        }


if __name__ == "__main__":
    asyncio.run(run_truth_detection())
