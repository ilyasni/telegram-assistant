"""
Integration тесты для Theme Subscriptions API.

Context7: Тестирование подключения/отключения подборок, проверка защиты от гонок,
корректной работы с ручными каналами и синхронизации.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import text
from uuid import uuid4
import uuid

from api.main import app
from models.database import get_db, SessionLocal


@pytest.fixture
def client():
    """Тестовый клиент FastAPI."""
    return TestClient(app)


@pytest.fixture
def db():
    """Тестовая сессия БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def sample_user(db: Session):
    """Создание тестового пользователя."""
    user_id = uuid4()
    tenant_id = uuid4()
    identity_id = uuid4()
    
    # Создаем tenant и identity если нужно
    db.execute(
        text("""
            INSERT INTO tenants (id, name, created_at)
            VALUES (:tenant_id, 'test_tenant', NOW())
            ON CONFLICT (id) DO NOTHING
        """),
        {"tenant_id": tenant_id}
    )
    
    db.execute(
        text("""
            INSERT INTO identities (id, telegram_id, created_at)
            VALUES (:identity_id, 123456789, NOW())
            ON CONFLICT (id) DO NOTHING
        """),
        {"identity_id": identity_id}
    )
    
    db.execute(
        text("""
            INSERT INTO users (id, tenant_id, telegram_id, identity_id, tier, created_at)
            VALUES (:user_id, :tenant_id, 123456789, :identity_id, 'free', NOW())
            ON CONFLICT DO NOTHING
        """),
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "identity_id": identity_id
        }
    )
    db.commit()
    
    return {
        "id": str(user_id),
        "telegram_id": 123456789,
        "tenant_id": str(tenant_id)
    }


@pytest.fixture
def sample_theme(db: Session):
    """Создание тестовой подборки."""
    theme_id = uuid4()
    
    db.execute(
        text("""
            INSERT INTO themes (id, slug, name, description, channels_count, created_at)
            VALUES (:theme_id, 'test_theme', 'Test Theme', 'Test description', 2, NOW())
            ON CONFLICT (slug) DO UPDATE SET id = :theme_id
        """),
        {"theme_id": theme_id}
    )
    
    # Создаем каналы в подборке
    channel1_id = uuid4()
    channel2_id = uuid4()
    
    db.execute(
        text("""
            INSERT INTO channels (id, username, title, is_active, created_at)
            VALUES 
                (:channel1_id, 'test_channel_1', 'Test Channel 1', true, NOW()),
                (:channel2_id, 'test_channel_2', 'Test Channel 2', true, NOW())
            ON CONFLICT DO NOTHING
        """),
        {
            "channel1_id": channel1_id,
            "channel2_id": channel2_id
        }
    )
    
    db.execute(
        text("""
            INSERT INTO theme_channels (id, theme_id, channel_username, title, subscribers, url, rank_in_theme, indexed_at)
            VALUES 
                (gen_random_uuid(), :theme_id, 'test_channel_1', 'Test Channel 1', 1000, 'https://t.me/test_channel_1', 1, NOW()),
                (gen_random_uuid(), :theme_id, 'test_channel_2', 'Test Channel 2', 2000, 'https://t.me/test_channel_2', 2, NOW())
            ON CONFLICT (theme_id, channel_username) DO NOTHING
        """),
        {"theme_id": theme_id}
    )
    
    db.commit()
    
    return {
        "id": str(theme_id),
        "slug": "test_theme"
    }


def test_subscribe_to_theme_success(client: TestClient, sample_user: dict, sample_theme: dict, db: Session):
    """Тест успешного подключения подборки."""
    response = client.post(
        f"/api/themes/{sample_theme['slug']}/subscribe/{sample_user['telegram_id']}"
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "subscribed"
    assert data["theme_slug"] == sample_theme["slug"]
    # Context7: Новый структурированный формат ответа
    assert "channels" in data
    assert data["channels"]["added"] >= 0
    assert data["channels"]["expected"] >= 0
    
    # Проверяем, что записи созданы
    user_theme_result = db.execute(
        text("""
            SELECT user_id, theme_id, is_active
            FROM user_theme
            WHERE user_id = :user_id AND theme_id = :theme_id
        """),
        {
            "user_id": sample_user["id"],
            "theme_id": sample_theme["id"]
        }
    )
    user_theme_row = user_theme_result.fetchone()
    assert user_theme_row is not None
    assert user_theme_row.is_active is True
    
    # Проверяем, что каналы подключены с source='theme'
    channels_result = db.execute(
        text("""
            SELECT COUNT(*) as count
            FROM user_channel
            WHERE user_id = :user_id 
              AND source = 'theme' 
              AND theme_id = :theme_id
              AND is_active = true
        """),
        {
            "user_id": sample_user["id"],
            "theme_id": sample_theme["id"]
        }
    )
    channels_count = channels_result.fetchone().count
    assert channels_count > 0


def test_unsubscribe_from_theme_success(client: TestClient, sample_user: dict, sample_theme: dict, db: Session):
    """Тест успешного отключения подборки."""
    # Сначала подключаем
    subscribe_response = client.post(
        f"/api/themes/{sample_theme['slug']}/subscribe/{sample_user['telegram_id']}"
    )
    assert subscribe_response.status_code == 201
    
    # Затем отключаем
    response = client.delete(
        f"/api/themes/{sample_theme['slug']}/unsubscribe/{sample_user['telegram_id']}"
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unsubscribed"
    
    # Проверяем, что user_theme деактивирован
    user_theme_result = db.execute(
        text("""
            SELECT is_active
            FROM user_theme
            WHERE user_id = :user_id AND theme_id = :theme_id
        """),
        {
            "user_id": sample_user["id"],
            "theme_id": sample_theme["id"]
        }
    )
    user_theme_row = user_theme_result.fetchone()
    assert user_theme_row is not None
    assert user_theme_row.is_active is False


def test_manual_channel_not_overwritten_by_theme(client: TestClient, sample_user: dict, sample_theme: dict, db: Session):
    """Тест: ручной канал не перезаписывается при подключении подборки."""
    # Создаем канал
    channel_id = uuid4()
    db.execute(
        text("""
            INSERT INTO channels (id, username, title, is_active, created_at)
            VALUES (:channel_id, 'test_channel_1', 'Test Channel 1', true, NOW())
            ON CONFLICT DO NOTHING
        """),
        {"channel_id": channel_id}
    )
    
    # Подключаем канал вручную
    db.execute(
        text("""
            INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
            VALUES (:user_id, :channel_id, 'manual', NULL, true, NOW(), NOW())
            ON CONFLICT (user_id, channel_id) WHERE source = 'manual'
            DO UPDATE SET is_active = true
        """),
        {
            "user_id": sample_user["id"],
            "channel_id": channel_id
        }
    )
    db.commit()
    
    # Подключаем подборку (которая содержит этот же канал)
    response = client.post(
        f"/api/themes/{sample_theme['slug']}/subscribe/{sample_user['telegram_id']}"
    )
    assert response.status_code == 201
    
    # Проверяем, что manual подписка осталась активной
    manual_result = db.execute(
        text("""
            SELECT is_active, source
            FROM user_channel
            WHERE user_id = :user_id 
              AND channel_id = :channel_id 
              AND source = 'manual'
        """),
        {
            "user_id": sample_user["id"],
            "channel_id": channel_id
        }
    )
    manual_row = manual_result.fetchone()
    assert manual_row is not None
    assert manual_row.is_active is True
    assert manual_row.source == 'manual'


def test_channel_limits_count_distinct(client: TestClient, sample_user: dict, db: Session):
    """Тест: лимиты считают уникальные каналы (COUNT DISTINCT)."""
    # Создаем канал
    channel_id = uuid4()
    db.execute(
        text("""
            INSERT INTO channels (id, username, title, is_active, created_at)
            VALUES (:channel_id, 'test_channel', 'Test Channel', true, NOW())
            ON CONFLICT DO NOTHING
        """),
        {"channel_id": channel_id}
    )
    
    # Подключаем канал дважды (manual и theme) - должно считаться как 1
    db.execute(
        text("""
            INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
            VALUES 
                (:user_id, :channel_id, 'manual', NULL, true, NOW(), NOW()),
                (:user_id, :channel_id, 'theme', :theme_id, true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """),
        {
            "user_id": sample_user["id"],
            "channel_id": channel_id,
            "theme_id": uuid4()
        }
    )
    db.commit()
    
    # Проверяем лимиты
    response = client.get(f"/api/channels/users/{sample_user['telegram_id']}/stats")
    assert response.status_code == 200
    data = response.json()
    # Должен быть 1 уникальный канал, не 2
    assert data["total"] == 1


def test_list_channels_with_source_filter(client: TestClient, sample_user: dict, db: Session):
    """Тест: фильтрация каналов по source."""
    # Создаем каналы
    channel1_id = uuid4()
    channel2_id = uuid4()
    
    db.execute(
        text("""
            INSERT INTO channels (id, username, title, is_active, created_at)
            VALUES 
                (:channel1_id, 'manual_channel', 'Manual Channel', true, NOW()),
                (:channel2_id, 'theme_channel', 'Theme Channel', true, NOW())
            ON CONFLICT DO NOTHING
        """),
        {
            "channel1_id": channel1_id,
            "channel2_id": channel2_id
        }
    )
    
    # Подключаем каналы с разными source
    db.execute(
        text("""
            INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
            VALUES 
                (:user_id, :channel1_id, 'manual', NULL, true, NOW(), NOW()),
                (:user_id, :channel2_id, 'theme', :theme_id, true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """),
        {
            "user_id": sample_user["id"],
            "channel1_id": channel1_id,
            "channel2_id": channel2_id,
            "theme_id": uuid4()
        }
    )
    db.commit()
    
    # Фильтр по manual
    response = client.get(f"/api/channels/users/{sample_user['telegram_id']}/list?source=manual")
    assert response.status_code == 200
    data = response.json()
    assert all(ch["source"] == "manual" for ch in data["channels"])
    
    # Фильтр по theme
    response = client.get(f"/api/channels/users/{sample_user['telegram_id']}/list?source=theme")
    assert response.status_code == 200
    data = response.json()
    assert all(ch["source"] == "theme" for ch in data["channels"])


def test_get_user_subscribed_themes(client: TestClient, sample_user: dict, sample_theme: dict, db: Session):
    """Тест: получение списка подключенных подборок."""
    # Подключаем подборку
    subscribe_response = client.post(
        f"/api/themes/{sample_theme['slug']}/subscribe/{sample_user['telegram_id']}"
    )
    assert subscribe_response.status_code == 201
    
    # Получаем список
    response = client.get(f"/api/themes/users/{sample_user['telegram_id']}/subscribed")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert any(theme["theme_slug"] == sample_theme["slug"] for theme in data["themes"])
