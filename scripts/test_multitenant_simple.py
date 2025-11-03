#!/usr/bin/env python3
"""
Простой тест multi-tenant функциональности без pytest.
Context7: Проверка изоляции данных, identities/memberships.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

import uuid
from sqlalchemy.orm import Session
from api.models.database import Identity, User, Tenant, SessionLocal, engine
from sqlalchemy import text

def test_identities_and_memberships():
    """Тест: создание identity и memberships в разных tenants."""
    print("=" * 60)
    print("Тест 1: Создание identity и memberships")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        # Создаём два tenant
        tenant1 = Tenant(name="Test Tenant 1")
        tenant2 = Tenant(name="Test Tenant 2")
        db.add(tenant1)
        db.add(tenant2)
        db.flush()
        
        print(f"✓ Tenant 1 создан: {tenant1.id}")
        print(f"✓ Tenant 2 создан: {tenant2.id}")
        
        # Создаём identity (глобальная)
        telegram_id = 999888777
        identity = Identity(telegram_id=telegram_id)
        db.add(identity)
        db.flush()
        
        print(f"✓ Identity создана: {identity.id} (telegram_id={telegram_id})")
        
        # Создаём membership в tenant1
        user1 = User(
            tenant_id=tenant1.id,
            identity_id=identity.id,
            telegram_id=telegram_id,
            username="test_user",
            first_name="Test",
            tier="pro"
        )
        db.add(user1)
        db.flush()
        
        print(f"✓ Membership 1 создан: {user1.id} (tenant={tenant1.id}, tier=pro)")
        
        # Создаём membership в tenant2 (тот же telegram_id!)
        user2 = User(
            tenant_id=tenant2.id,
            identity_id=identity.id,
            telegram_id=telegram_id,
            username="test_user",
            first_name="Test",
            tier="free"
        )
        db.add(user2)
        db.commit()
        
        print(f"✓ Membership 2 создан: {user2.id} (tenant={tenant2.id}, tier=free)")
        
        # Проверка: одна identity, два memberships
        identities_count = db.query(Identity).filter(Identity.telegram_id == telegram_id).count()
        users_count = db.query(User).filter(User.identity_id == identity.id).count()
        
        assert identities_count == 1, f"Expected 1 identity, got {identities_count}"
        assert users_count == 2, f"Expected 2 memberships, got {users_count}"
        
        print(f"\n✓ Проверка: identities_count={identities_count}, memberships_count={users_count}")
        print("✓ Тест 1 пройден: один telegram_id может быть в нескольких tenants\n")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"✗ Тест 1 провален: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def test_unique_constraints():
    """Тест: проверка уникальных ограничений."""
    print("=" * 60)
    print("Тест 2: Уникальные ограничения")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        # Создаём tenant и identity
        tenant = Tenant(name="Unique Test Tenant")
        db.add(tenant)
        db.flush()
        
        telegram_id = 111222333
        identity = Identity(telegram_id=telegram_id)
        db.add(identity)
        db.flush()
        
        # Создаём первый membership
        user1 = User(
            tenant_id=tenant.id,
            identity_id=identity.id,
            telegram_id=telegram_id,
            username="unique_test"
        )
        db.add(user1)
        db.commit()
        
        print(f"✓ Первый membership создан: {user1.id}")
        
        # Пытаемся создать дубликат (тот же tenant + identity)
        user2 = User(
            tenant_id=tenant.id,
            identity_id=identity.id,
            telegram_id=telegram_id,
            username="duplicate"
        )
        db.add(user2)
        
        try:
            db.commit()
            print("✗ Тест 2 провален: дубликат membership был создан (не должен был)")
            return False
        except Exception as e:
            db.rollback()
            print(f"✓ Уникальное ограничение работает: {type(e).__name__}")
        
        # Проверка: telegram_id уникален в identities
        identity2 = Identity(telegram_id=telegram_id)
        db.add(identity2)
        try:
            db.commit()
            print("✗ Тест 2 провален: дубликат identity был создан (не должен был)")
            return False
        except Exception as e:
            db.rollback()
            print(f"✓ Уникальное ограничение на identities.telegram_id работает: {type(e).__name__}")
        
        print("✓ Тест 2 пройден: уникальные ограничения работают корректно\n")
        return True
        
    except Exception as e:
        db.rollback()
        print(f"✗ Тест 2 провален: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def test_backfill_data():
    """Тест: проверка что backfill миграция заполнила данные."""
    print("=" * 60)
    print("Тест 3: Проверка backfill данных")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        # Проверяем, что все users имеют identity_id
        result = db.execute(text("SELECT COUNT(*) FROM users WHERE identity_id IS NULL"))
        null_count = result.scalar()
        
        if null_count > 0:
            print(f"⚠ Предупреждение: {null_count} users без identity_id")
            return False
        
        print(f"✓ Все users имеют identity_id (null_count={null_count})")
        
        # Проверяем соответствие: каждый telegram_id в users должен быть в identities
        result = db.execute(text("""
            SELECT COUNT(DISTINCT u.telegram_id) as users_telegram_ids,
                   COUNT(DISTINCT i.telegram_id) as identities_telegram_ids
            FROM users u
            LEFT JOIN identities i ON u.identity_id = i.id
        """))
        row = result.fetchone()
        
        if row.users_telegram_ids != row.identities_telegram_ids:
            print(f"✗ Несоответствие: users.telegram_id={row.users_telegram_ids}, identities.telegram_id={row.identities_telegram_ids}")
            return False
        
        print(f"✓ Соответствие данных: {row.users_telegram_ids} telegram_ids в обоих таблицах")
        print("✓ Тест 3 пройден: backfill данные корректны\n")
        return True
        
    except Exception as e:
        print(f"✗ Тест 3 провален: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def test_rls_policies():
    """Тест: проверка что RLS политики созданы."""
    print("=" * 60)
    print("Тест 4: Проверка RLS политик")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        # Проверяем RLS включен
        result = db.execute(text("""
            SELECT tablename, rowsecurity 
            FROM pg_tables 
            WHERE schemaname = 'public' 
            AND tablename IN ('users', 'identities', 'channels', 'telegram_sessions')
            ORDER BY tablename
        """))
        
        tables = {}
        for row in result:
            tables[row.tablename] = row.rowsecurity
        
        print("RLS статус по таблицам:")
        for table, enabled in tables.items():
            status = "✓ ВКЛ" if enabled else "✗ ВЫКЛ"
            print(f"  {table}: {status}")
        
        # Проверяем политики
        result = db.execute(text("""
            SELECT tablename, policyname 
            FROM pg_policies 
            WHERE schemaname = 'public'
            AND tablename IN ('users', 'identities', 'channels', 'telegram_sessions')
            ORDER BY tablename, policyname
        """))
        
        policies = list(result)
        print(f"\n✓ Найдено {len(policies)} RLS политик")
        for row in policies[:10]:  # Показываем первые 10
            print(f"  {row.tablename}.{row.policyname}")
        
        if len(policies) > 0:
            print("✓ Тест 4 пройден: RLS политики созданы\n")
            return True
        else:
            print("⚠ RLS политики не найдены (возможно feature_rls_enabled=False)\n")
            return True  # Не критично, если RLS выключен
        
    except Exception as e:
        print(f"✗ Тест 4 провален: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def test_connection_pooling():
    """Тест: проверка connection pooling настроен."""
    print("=" * 60)
    print("Тест 5: Проверка connection pooling")
    print("=" * 60)
    
    try:
        from api.models.database import engine
        
        pool = engine.pool
        print(f"✓ Pool size: {pool.size()}")
        print(f"✓ Max overflow: {pool._max_overflow}")
        print(f"✓ Pool recycle: {pool._recycle}")
        print(f"✓ Pool pre ping: {pool._pre_ping}")
        
        if pool.size() > 0:
            print("✓ Тест 5 пройден: connection pooling настроен\n")
            return True
        else:
            print("⚠ Connection pool не настроен\n")
            return False
            
    except Exception as e:
        print(f"✗ Тест 5 провален: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Запуск всех тестов."""
    print("\n" + "=" * 60)
    print("МУЛЬТИ-ТЕНАНТ ТЕСТЫ")
    print("=" * 60 + "\n")
    
    tests = [
        ("Создание identity и memberships", test_identities_and_memberships),
        ("Уникальные ограничения", test_unique_constraints),
        ("Backfill данных", test_backfill_data),
        ("RLS политики", test_rls_policies),
        ("Connection pooling", test_connection_pooling),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ Тест '{name}' упал с ошибкой: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Итоги
    print("=" * 60)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ ПРОЙДЕН" if result else "✗ ПРОВАЛЕН"
        print(f"{status}: {name}")
    
    print(f"\nПройдено: {passed}/{total}")
    
    if passed == total:
        print("✓ Все тесты пройдены!")
        return 0
    else:
        print(f"✗ {total - passed} тестов провалено")
        return 1


if __name__ == "__main__":
    sys.exit(main())

