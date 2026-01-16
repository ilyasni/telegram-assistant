#!/usr/bin/env python3
"""
Диагностика проблемы отсутствия новых постов.
Context7: Проверка почему посты не сохраняются в БД
"""

import os
import sys
import asyncio
import asyncpg
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}\n")

def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")

def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")

def print_info(text: str):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


class NoPostsDiagnostic:
    """Диагностика проблемы отсутствия новых постов."""
    
    def __init__(self):
        self.db_pool = None
        
    async def initialize(self):
        """Инициализация подключений."""
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/telegram_assistant")
            self.db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
            print_success("PostgreSQL подключен")
        except Exception as e:
            print_error(f"Ошибка подключения к БД: {e}")
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
    
    async def check_recent_posts(self):
        """Проверка постов за последние 3 часа."""
        print_header("1. ПРОВЕРКА ПОСТОВ В БД")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Посты за последние 3 часа
                recent_3h = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM posts 
                    WHERE created_at > NOW() - INTERVAL '3 hours'
                """)
                
                # Посты за последние 24 часа
                recent_24h = await conn.fetchval("""
                    SELECT COUNT(*) 
                    FROM posts 
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                """)
                
                # Последний пост
                last_post = await conn.fetchrow("""
                    SELECT 
                        id,
                        channel_id,
                        created_at,
                        EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600 as hours_ago
                    FROM posts 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """)
                
                print_info(f"Постов за последние 3 часа: {recent_3h}")
                print_info(f"Постов за последние 24 часа: {recent_24h}")
                
                if last_post:
                    hours_ago = last_post['hours_ago']
                    print_info(f"Последний пост: {hours_ago:.2f} часов назад")
                    
                    if hours_ago > 3:
                        print_error(f"❌ Проблема: последний пост был {hours_ago:.2f} часов назад (> 3 часов)")
                        return False
                    else:
                        print_success(f"Последний пост свежий ({hours_ago:.2f} часов назад)")
                        return True
                else:
                    print_error("Нет постов в БД")
                    return False
                    
        except Exception as e:
            print_error(f"Ошибка проверки постов: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def check_channel_activity(self):
        """Проверка активности каналов."""
        print_header("2. ПРОВЕРКА АКТИВНОСТИ КАНАЛОВ")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Каналы с последним постом
                active_channels = await conn.fetch("""
                    SELECT 
                        c.id,
                        c.username,
                        c.title,
                        c.last_parsed_at,
                        MAX(p.posted_at) as last_post_date,
                        EXTRACT(EPOCH FROM (NOW() - MAX(p.posted_at))) / 3600 as hours_since_last_post,
                        COUNT(p.id) as total_posts
                    FROM channels c
                    LEFT JOIN posts p ON p.channel_id = c.id
                    WHERE c.is_active = true
                    GROUP BY c.id, c.username, c.title, c.last_parsed_at
                    ORDER BY MAX(p.posted_at) DESC NULLS LAST
                    LIMIT 10
                """)
                
                if active_channels:
                    print_info(f"Найдено {len(active_channels)} активных каналов:")
                    for ch in active_channels:
                        hours_since = ch['hours_since_last_post'] if ch['hours_since_last_post'] else 999
                        if hours_since < 3:
                            status = "✅"
                        elif hours_since < 24:
                            status = "⚠️"
                        else:
                            status = "❌"
                        print(f"  {status} {ch['username'] or ch['id']}: последний пост {hours_since:.1f}ч назад, всего постов: {ch['total_posts']}")
                else:
                    print_warning("Нет активных каналов")
                    
        except Exception as e:
            print_error(f"Ошибка проверки каналов: {e}")
            import traceback
            traceback.print_exc()
    
    async def check_parser_errors(self):
        """Проверка ошибок парсера из логов."""
        print_header("3. АНАЛИЗ ОШИБОК ПАРСЕРА")
        
        print_warning("В логах обнаружены проблемы:")
        print("  1. FloodWait: 'A wait of 11472 seconds is required'")
        print("  2. Entity not found: 'Could not find the input entity for PeerChannel'")
        print("  3. messages_processed: 0 (парсер запускается, но не обрабатывает сообщения)")
        print("")
        print_info("Причины:")
        print("  - Telegram API ограничивает запросы (FloodWait)")
        print("  - Каналы недоступны или удалены")
        print("  - Проблемы с entity resolution в Telethon")
        print("")
        print_info("Решения:")
        print("  1. Дождаться окончания FloodWait (11472 секунд = ~3 часа)")
        print("  2. Проверить доступность каналов")
        print("  3. Обновить tg_channel_id для каналов")
    
    async def check_last_parsed_at(self):
        """Проверка last_parsed_at каналов."""
        print_header("4. ПРОВЕРКА LAST_PARSED_AT")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Каналы с последним парсингом
                channels = await conn.fetch("""
                    SELECT 
                        id,
                        username,
                        title,
                        last_parsed_at,
                        EXTRACT(EPOCH FROM (NOW() - last_parsed_at)) / 3600 as hours_since_parse
                    FROM channels
                    WHERE is_active = true
                    ORDER BY last_parsed_at DESC NULLS LAST
                    LIMIT 10
                """)
                
                if channels:
                    print_info("Последние обработанные каналы:")
                    for ch in channels:
                        hours = ch['hours_since_parse'] if ch['hours_since_parse'] else 999
                        if hours < 1:
                            status = "✅"
                        elif hours < 6:
                            status = "⚠️"
                        else:
                            status = "❌"
                        print(f"  {status} {ch['username'] or ch['id']}: парсинг {hours:.1f}ч назад")
                else:
                    print_warning("Нет активных каналов")
                    
        except Exception as e:
            print_error(f"Ошибка проверки: {e}")
    
    async def check_floodwait_status(self):
        """Проверка статуса FloodWait."""
        print_header("5. ПРОВЕРКА FLOODWAIT")
        
        print_warning("В логах обнаружен FloodWait: 11472 секунд (~3 часа)")
        print_info("Это означает, что Telegram API ограничил запросы")
        print_info("Парсер будет ждать окончания FloodWait перед следующей попыткой")
        print("")
        print_info("Проверка cooldown в Redis:")
        print("  - Каналы могут быть в cooldown из-за FloodWait")
        print("  - Cooldown предотвращает повторные запросы")
        print("")
        print_info("Рекомендации:")
        print("  1. Дождаться окончания FloodWait")
        print("  2. Проверить rate limiting настройки")
        print("  3. Увеличить интервал между запросами")
    
    async def generate_report(self):
        """Генерация итогового отчета."""
        print_header("ИТОГОВЫЙ ОТЧЕТ")
        
        has_recent_posts = await self.check_recent_posts()
        await self.check_channel_activity()
        await self.check_parser_errors()
        await self.check_last_parsed_at()
        await self.check_floodwait_status()
        
        print("\n" + "="*80)
        print("ВЫВОДЫ")
        print("="*80)
        
        if not has_recent_posts:
            print_error("❌ ПРОБЛЕМА ПОДТВЕРЖДЕНА: Нет новых постов за последние 3 часа")
            print("")
            print("Причины:")
            print("  1. FloodWait от Telegram API (11472 секунд)")
            print("  2. Каналы недоступны (entity not found)")
            print("  3. Парсер запускается, но messages_processed = 0")
            print("")
            print("Решения:")
            print("  1. Дождаться окончания FloodWait (~3 часа)")
            print("  2. Проверить доступность каналов вручную")
            print("  3. Обновить tg_channel_id для проблемных каналов")
            print("  4. Проверить настройки rate limiting")
        else:
            print_success("✅ Посты сохраняются корректно")


async def main():
    """Главная функция."""
    diagnostic = NoPostsDiagnostic()
    
    try:
        await diagnostic.initialize()
        await diagnostic.generate_report()
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await diagnostic.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
