#!/usr/bin/env python3
"""CLI утилита для управления инвайт-кодами Telegram Assistant.

Использование:
    python scripts/invites_cli.py create --tenant <id> --role <r> --limit 10 --expires 2025-12-31
    python scripts/invites_cli.py revoke --code <code>
    python scripts/invites_cli.py list --tenant <id> --status active
    python scripts/invites_cli.py get --code <code>
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import psycopg2
from psycopg2.extras import RealDictCursor
import structlog

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class InvitesCLI:
    """CLI для управления инвайт-кодами."""
    
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/telegram_assistant")
    
    def get_db_connection(self):
        """Получение подключения к БД."""
        try:
            return psycopg2.connect(self.db_url)
        except Exception as e:
            logger.error("Failed to connect to database", error=str(e))
            print(f"❌ Ошибка подключения к БД: {e}")
            sys.exit(1)
    
    def create_invite(self, tenant_id: str, role: str, uses_limit: int, expires_at: Optional[str], notes: Optional[str]) -> Dict[str, Any]:
        """Создание нового инвайт-кода."""
        conn = self.get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Генерируем уникальный код
                import random
                import string
                
                def generate_code():
                    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
                
                code = generate_code()
                
                # Проверяем уникальность
                cursor.execute("SELECT code FROM invite_codes WHERE code = %s", (code,))
                while cursor.fetchone():
                    code = generate_code()
                    cursor.execute("SELECT code FROM invite_codes WHERE code = %s", (code,))
                
                # Парсим дату истечения
                expires_datetime = None
                if expires_at:
                    try:
                        expires_datetime = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    except ValueError:
                        print(f"❌ Неверный формат даты: {expires_at}. Используйте ISO формат (2025-12-31T23:59:59Z)")
                        sys.exit(1)
                
                # Создаём инвайт
                cursor.execute(
                    """
                    INSERT INTO invite_codes 
                    (code, tenant_id, role, uses_limit, expires_at, notes, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        code,
                        tenant_id,
                        role,
                        uses_limit,
                        expires_datetime,
                        notes,
                        datetime.now(timezone.utc)
                    )
                )
                
                result = cursor.fetchone()
                conn.commit()
                
                logger.info("Invite code created", code=code, tenant_id=tenant_id, role=role)
                
                return {
                    "code": result['code'],
                    "tenant_id": str(result['tenant_id']),
                    "role": result['role'],
                    "uses_limit": result['uses_limit'],
                    "uses_count": result['uses_count'],
                    "active": result['active'],
                    "expires_at": result['expires_at'].isoformat() if result['expires_at'] else None,
                    "created_at": result['created_at'].isoformat(),
                    "notes": result['notes']
                }
                
        except Exception as e:
            conn.rollback()
            logger.error("Failed to create invite", error=str(e))
            print(f"❌ Ошибка создания инвайта: {e}")
            sys.exit(1)
        finally:
            conn.close()
    
    def revoke_invite(self, code: str) -> Dict[str, Any]:
        """Отзыв инвайт-кода."""
        conn = self.get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Проверяем существование
                cursor.execute("SELECT active FROM invite_codes WHERE code = %s", (code,))
                result = cursor.fetchone()
                
                if not result:
                    print(f"❌ Инвайт-код не найден: {code}")
                    sys.exit(1)
                
                if not result['active']:
                    print(f"❌ Инвайт-код уже отозван: {code}")
                    sys.exit(1)
                
                # Отзываем
                cursor.execute("UPDATE invite_codes SET active = false WHERE code = %s", (code,))
                conn.commit()
                
                logger.info("Invite code revoked", code=code)
                
                return {
                    "code": code,
                    "status": "revoked",
                    "revoked_at": datetime.now(timezone.utc).isoformat()
                }
                
        except Exception as e:
            conn.rollback()
            logger.error("Failed to revoke invite", code=code, error=str(e))
            print(f"❌ Ошибка отзыва инвайта: {e}")
            sys.exit(1)
        finally:
            conn.close()
    
    def get_invite(self, code: str) -> Dict[str, Any]:
        """Получение информации об инвайт-коде."""
        conn = self.get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM invite_codes WHERE code = %s", (code,))
                result = cursor.fetchone()
                
                if not result:
                    print(f"❌ Инвайт-код не найден: {code}")
                    sys.exit(1)
                
                return {
                    "code": result['code'],
                    "tenant_id": str(result['tenant_id']),
                    "role": result['role'],
                    "uses_limit": result['uses_limit'],
                    "uses_count": result['uses_count'],
                    "active": result['active'],
                    "expires_at": result['expires_at'].isoformat() if result['expires_at'] else None,
                    "created_at": result['created_at'].isoformat(),
                    "last_used_at": result['last_used_at'].isoformat() if result['last_used_at'] else None,
                    "notes": result['notes']
                }
                
        except Exception as e:
            logger.error("Failed to get invite", code=code, error=str(e))
            print(f"❌ Ошибка получения инвайта: {e}")
            sys.exit(1)
        finally:
            conn.close()
    
    def list_invites(self, tenant_id: Optional[str], status: Optional[str], limit: int) -> Dict[str, Any]:
        """Получение списка инвайт-кодов."""
        conn = self.get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Построение WHERE условия
                where_conditions = []
                params = []
                
                if tenant_id:
                    where_conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                
                if status:
                    if status == "active":
                        where_conditions.append("active = true AND (expires_at IS NULL OR expires_at > NOW())")
                    elif status == "revoked":
                        where_conditions.append("active = false")
                    elif status == "expired":
                        where_conditions.append("expires_at IS NOT NULL AND expires_at <= NOW()")
                
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                
                # Получение записей
                cursor.execute(
                    f"""
                    SELECT * FROM invite_codes 
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params + [limit]
                )
                
                results = cursor.fetchall()
                
                invites = []
                for row in results:
                    invites.append({
                        "code": row['code'],
                        "tenant_id": str(row['tenant_id']),
                        "role": row['role'],
                        "uses_limit": row['uses_limit'],
                        "uses_count": row['uses_count'],
                        "active": row['active'],
                        "expires_at": row['expires_at'].isoformat() if row['expires_at'] else None,
                        "created_at": row['created_at'].isoformat(),
                        "last_used_at": row['last_used_at'].isoformat() if row['last_used_at'] else None,
                        "notes": row['notes']
                    })
                
                return {
                    "invites": invites,
                    "total": len(invites)
                }
                
        except Exception as e:
            logger.error("Failed to list invites", error=str(e))
            print(f"❌ Ошибка получения списка инвайтов: {e}")
            sys.exit(1)
        finally:
            conn.close()


def print_table(data: list, headers: list):
    """Вывод данных в виде таблицы."""
    if not data:
        print("Нет данных для отображения")
        return
    
    # Вычисляем ширину колонок
    widths = [len(header) for header in headers]
    for row in data:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    
    # Выводим заголовки
    header_row = " | ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    print(header_row)
    print("-" * len(header_row))
    
    # Выводим данные
    for row in data:
        data_row = " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(row))
        print(data_row)


def main():
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(description="CLI для управления инвайт-кодами Telegram Assistant")
    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")
    
    # Команда create
    create_parser = subparsers.add_parser("create", help="Создать новый инвайт-код")
    create_parser.add_argument("--tenant", required=True, help="ID арендатора")
    create_parser.add_argument("--role", default="user", choices=["user", "admin"], help="Роль пользователя")
    create_parser.add_argument("--limit", type=int, default=1, help="Лимит использований (0 = безлимит)")
    create_parser.add_argument("--expires", help="Дата истечения (ISO формат: 2025-12-31T23:59:59Z)")
    create_parser.add_argument("--notes", help="Заметки")
    create_parser.add_argument("--json", action="store_true", help="Вывод в JSON формате")
    
    # Команда revoke
    revoke_parser = subparsers.add_parser("revoke", help="Отозвать инвайт-код")
    revoke_parser.add_argument("--code", required=True, help="Код инвайта")
    revoke_parser.add_argument("--json", action="store_true", help="Вывод в JSON формате")
    
    # Команда get
    get_parser = subparsers.add_parser("get", help="Получить информацию об инвайт-коде")
    get_parser.add_argument("--code", required=True, help="Код инвайта")
    get_parser.add_argument("--json", action="store_true", help="Вывод в JSON формате")
    
    # Команда list
    list_parser = subparsers.add_parser("list", help="Получить список инвайт-кодов")
    list_parser.add_argument("--tenant", help="Фильтр по tenant_id")
    list_parser.add_argument("--status", choices=["active", "revoked", "expired"], help="Фильтр по статусу")
    list_parser.add_argument("--limit", type=int, default=50, help="Количество записей")
    list_parser.add_argument("--json", action="store_true", help="Вывод в JSON формате")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    cli = InvitesCLI()
    
    try:
        if args.command == "create":
            result = cli.create_invite(
                tenant_id=args.tenant,
                role=args.role,
                uses_limit=args.limit,
                expires_at=args.expires,
                notes=args.notes
            )
            
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print("✅ Инвайт-код создан:")
                print(f"Код: {result['code']}")
                print(f"Tenant: {result['tenant_id']}")
                print(f"Роль: {result['role']}")
                print(f"Лимит: {result['uses_limit']}")
                print(f"Истекает: {result['expires_at'] or 'Никогда'}")
                if result['notes']:
                    print(f"Заметки: {result['notes']}")
        
        elif args.command == "revoke":
            result = cli.revoke_invite(code=args.code)
            
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(f"✅ Инвайт-код отозван: {result['code']}")
        
        elif args.command == "get":
            result = cli.get_invite(code=args.code)
            
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print("📋 Информация об инвайт-коде:")
                print(f"Код: {result['code']}")
                print(f"Tenant: {result['tenant_id']}")
                print(f"Роль: {result['role']}")
                print(f"Лимит: {result['uses_limit']}")
                print(f"Использовано: {result['uses_count']}")
                print(f"Активен: {'Да' if result['active'] else 'Нет'}")
                print(f"Истекает: {result['expires_at'] or 'Никогда'}")
                print(f"Создан: {result['created_at']}")
                if result['last_used_at']:
                    print(f"Последнее использование: {result['last_used_at']}")
                if result['notes']:
                    print(f"Заметки: {result['notes']}")
        
        elif args.command == "list":
            result = cli.list_invites(
                tenant_id=args.tenant,
                status=args.status,
                limit=args.limit
            )
            
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                if not result['invites']:
                    print("📋 Инвайт-коды не найдены")
                else:
                    print(f"📋 Найдено инвайт-кодов: {result['total']}")
                    print()
                    
                    # Подготавливаем данные для таблицы
                    table_data = []
                    for invite in result['invites']:
                        table_data.append([
                            invite['code'],
                            invite['tenant_id'][:8] + "...",
                            invite['role'],
                            f"{invite['uses_count']}/{invite['uses_limit']}",
                            "✅" if invite['active'] else "❌",
                            invite['expires_at'][:10] if invite['expires_at'] else "∞",
                            invite['created_at'][:10]
                        ])
                    
                    headers = ["Код", "Tenant", "Роль", "Использовано", "Активен", "Истекает", "Создан"]
                    print_table(table_data, headers)
    
    except KeyboardInterrupt:
        print("\n❌ Операция прервана пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error("CLI error", error=str(e))
        print(f"❌ Неожиданная ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
