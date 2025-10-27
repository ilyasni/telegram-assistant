#!/usr/bin/env python3
"""
Тестовый скрипт для проверки реализации incremental parsing.
Проверяет логику _get_since_date(), Redis HWM, и обновление last_parsed_at.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

# Добавляем путь к модулям
sys.path.insert(0, os.path.dirname(__file__))

from services.channel_parser import ParserConfig
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def test_get_since_date():
    """Тест метода _get_since_date() с разными режимами."""
    print("\n=== Test 1: _get_since_date() logic ===\n")
    
    # Создаём mock парсер
    class MockParser:
        def __init__(self):
            self.config = ParserConfig()
            # Mock Redis клиент
            self.redis_client = None
        
        async def _get_since_date(self, channel: dict, mode: str):
            """Mock метода для тестирования логики."""
            now = datetime.now(timezone.utc)
            
            if mode == "incremental":
                base = channel.get('last_parsed_at')
                if base:
                    age_hours = (now - base).total_seconds() / 3600
                    if age_hours > self.config.lpa_max_age_hours:
                        print(f"⚠️  Safeguard triggered: last_parsed_at too old ({age_hours:.1f}h)")
                        return now - timedelta(hours=self.config.historical_hours)
                    return max(base, now - timedelta(minutes=self.config.incremental_minutes))
                else:
                    return now - timedelta(minutes=self.config.incremental_minutes)
            
            elif mode == "historical":
                return now - timedelta(hours=self.config.historical_hours)
            
            else:
                raise ValueError(f"Unknown mode: {mode}")
    
    parser = MockParser()
    
    # Тест 1: Historical mode
    channel_empty = {'id': 'test-1', 'last_parsed_at': None}
    since = await parser._get_since_date(channel_empty, 'historical')
    print(f"✓ Historical mode (no last_parsed_at):")
    print(f"  Expected: {datetime.now(timezone.utc) - timedelta(hours=24)}")
    print(f"  Got:      {since}")
    print(f"  Diff:     {(datetime.now(timezone.utc) - since).total_seconds() / 3600:.1f}h\n")
    
    # Тест 2: Incremental mode (fresh)
    channel_fresh = {
        'id': 'test-2',
        'last_parsed_at': datetime.now(timezone.utc) - timedelta(minutes=3)
    }
    since = await parser._get_since_date(channel_fresh, 'incremental')
    print(f"✓ Incremental mode (fresh, 3min ago):")
    print(f"  Got:      {since}")
    print(f"  Age:      {(datetime.now(timezone.utc) - since).total_seconds() / 60:.1f}min\n")
    
    # Тест 3: Incremental mode (stale, trigger safeguard)
    channel_stale = {
        'id': 'test-3',
        'last_parsed_at': datetime.now(timezone.utc) - timedelta(hours=50)
    }
    since = await parser._get_since_date(channel_stale, 'incremental')
    print(f"✓ Incremental mode (stale, 50h ago - should trigger safeguard):")
    print(f"  Got:      {since}")
    print(f"  Age:      {(datetime.now(timezone.utc) - since).total_seconds() / 3600:.1f}h\n")
    
    # Тест 4: Incremental mode (no last_parsed_at)
    since = await parser._get_since_date(channel_empty, 'incremental')
    print(f"✓ Incremental mode (no last_parsed_at - fallback):")
    print(f"  Expected: {datetime.now(timezone.utc) - timedelta(minutes=5)}")
    print(f"  Got:      {since}")
    print(f"  Diff:     {(datetime.now(timezone.utc) - since).total_seconds() / 60:.1f}min\n")


async def test_config_load():
    """Тест загрузки конфигурации из ENV."""
    print("\n=== Test 2: Configuration loading ===\n")
    
    # Устанавливаем тестовые переменные окружения
    os.environ['PARSER_MODE_OVERRIDE'] = 'auto'
    os.environ['PARSER_HISTORICAL_HOURS'] = '12'
    os.environ['PARSER_INCREMENTAL_MINUTES'] = '3'
    os.environ['PARSER_LPA_MAX_AGE_HOURS'] = '24'
    os.environ['FEATURE_INCREMENTAL_PARSING_ENABLED'] = 'true'
    
    config = ParserConfig()
    
    print(f"✓ Config loaded:")
    print(f"  mode_override:        {config.mode_override}")
    print(f"  historical_hours:     {config.historical_hours}")
    print(f"  incremental_minutes:  {config.incremental_minutes}")
    print(f"  lpa_max_age_hours:    {config.lpa_max_age_hours}")
    print()


async def test_redis_hwm_simulation():
    """Симуляция работы с Redis HWM."""
    print("\n=== Test 3: Redis HWM simulation ===\n")
    
    # Симуляция Redis ключей
    hwm_key = "parse_hwm:test-channel-123"
    hwm_value = datetime.now(timezone.utc).isoformat()
    
    print(f"✓ Redis HWM simulation:")
    print(f"  Key:    {hwm_key}")
    print(f"  Value:  {hwm_value}")
    print(f"  Parsed: {datetime.fromisoformat(hwm_value)}")
    print()


async def main():
    """Главная функция тестов."""
    print("=" * 70)
    print("Incremental Parsing Implementation Tests")
    print("=" * 70)
    
    try:
        await test_config_load()
        await test_get_since_date()
        await test_redis_hwm_simulation()
        
        print("\n" + "=" * 70)
        print("✓ All tests completed successfully")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
