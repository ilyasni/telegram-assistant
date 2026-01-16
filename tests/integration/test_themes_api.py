"""
Integration тесты для Themes API endpoints.
Context7: Тестирование полного цикла работы с темами и каналами через HTTP API.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from uuid import uuid4

from api.main import app
from models.database import get_db


@pytest.fixture
def client():
    """Фикстура для создания test client."""
    return TestClient(app)


@pytest.fixture
def db_session():
    """Фикстура для получения сессии БД."""
    # Context7: Используем реальную сессию БД для integration тестов
    db_gen = get_db()
    db = next(db_gen)
    try:
        yield db
    finally:
        db.close()
        try:
            next(db_gen)
        except StopIteration:
            pass


@pytest.fixture
def sample_theme_data(db_session):
    """Фикстура для создания тестовой темы в БД."""
    from sqlalchemy import text
    
    theme_id = uuid4()
    theme_slug = f"test_theme_{uuid4().hex[:8]}"
    
    # Context7: Вставка тестовой темы напрямую в БД
    db_session.execute(
        text("""
            INSERT INTO themes (id, slug, name, description, channels_count, indexed_at, created_at)
            VALUES (:id, :slug, :name, :description, :channels_count, :indexed_at, :created_at)
        """),
        {
            'id': theme_id,
            'slug': theme_slug,
            'name': 'Test Theme',
            'description': 'Test theme description',
            'channels_count': 0,
            'indexed_at': datetime.now(timezone.utc),
            'created_at': datetime.now(timezone.utc)
        }
    )
    db_session.commit()
    
    yield {
        'id': str(theme_id),
        'slug': theme_slug,
        'name': 'Test Theme'
    }
    
    # Context7: Очистка после теста
    db_session.execute(
        text("DELETE FROM themes WHERE id = :id"),
        {'id': theme_id}
    )
    db_session.commit()


@pytest.fixture
def sample_channel_data(db_session, sample_theme_data):
    """Фикстура для создания тестового канала в БД."""
    from sqlalchemy import text
    
    channel_id = uuid4()
    theme_id = sample_theme_data['id']
    channel_username = f"test_channel_{uuid4().hex[:8]}"
    
    # Context7: Вставка тестового канала напрямую в БД
    db_session.execute(
        text("""
            INSERT INTO theme_channels 
            (id, theme_id, channel_username, title, subscribers, er, url, rank_in_theme, indexed_at)
            VALUES (:id, :theme_id, :channel_username, :title, :subscribers, :er, :url, :rank_in_theme, :indexed_at)
        """),
        {
            'id': channel_id,
            'theme_id': theme_id,
            'channel_username': channel_username,
            'title': 'Test Channel',
            'subscribers': 100000,
            'er': 5.5,
            'url': f'https://t.me/{channel_username}',
            'rank_in_theme': 1,
            'indexed_at': datetime.now(timezone.utc)
        }
    )
    db_session.commit()
    
    yield {
        'id': str(channel_id),
        'channel_username': channel_username,
        'title': 'Test Channel'
    }
    
    # Context7: Очистка после теста
    db_session.execute(
        text("DELETE FROM theme_channels WHERE id = :id"),
        {'id': channel_id}
    )
    db_session.commit()


class TestThemesAPI:
    """Тесты для Themes API endpoints."""
    
    def test_get_themes_list_success(self, client, sample_theme_data):
        """Тест успешного получения списка тем."""
        # Вызов API
        response = client.get("/api/themes?limit=10&offset=0")
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert 'themes' in data
        assert 'total' in data
        assert 'limit' in data
        assert 'offset' in data
        assert isinstance(data['themes'], list)
        assert data['limit'] == 10
        assert data['offset'] == 0
    
    def test_get_themes_list_with_search(self, client, sample_theme_data):
        """Тест получения тем с поиском."""
        # Вызов API с поиском
        response = client.get("/api/themes?search=Test")
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert 'themes' in data
        # Context7: Проверяем, что результаты содержат поисковый запрос
        if data['themes']:
            theme = data['themes'][0]
            assert 'Test' in theme['name'] or (theme.get('description') and 'Test' in theme['description'])
    
    def test_get_theme_by_slug_success(self, client, sample_theme_data):
        """Тест успешного получения темы по slug."""
        # Вызов API
        response = client.get(f"/api/themes/{sample_theme_data['slug']}")
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert data['slug'] == sample_theme_data['slug']
        assert data['name'] == sample_theme_data['name']
        assert 'id' in data
        assert 'channels_count' in data
    
    def test_get_theme_by_slug_not_found(self, client):
        """Тест получения несуществующей темы."""
        # Вызов API с несуществующим slug
        response = client.get("/api/themes/nonexistent_theme_12345")
        
        # Проверки
        assert response.status_code == 404
        data = response.json()
        assert 'detail' in data
    
    def test_get_theme_by_slug_invalid(self, client):
        """Тест получения темы с невалидным slug."""
        # Вызов API с невалидным slug (содержит спецсимволы)
        response = client.get("/api/themes/invalid@slug#123")
        
        # Проверки
        assert response.status_code == 400
        data = response.json()
        assert 'detail' in data
    
    def test_get_theme_channels_success(self, client, sample_theme_data, sample_channel_data):
        """Тест успешного получения каналов темы."""
        # Вызов API
        response = client.get(f"/api/themes/{sample_theme_data['slug']}/channels")
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert 'channels' in data
        assert 'total' in data
        assert 'limit' in data
        assert 'offset' in data
        assert 'theme_slug' in data
        assert data['theme_slug'] == sample_theme_data['slug']
        assert isinstance(data['channels'], list)
    
    def test_get_theme_channels_with_filters(self, client, sample_theme_data, sample_channel_data):
        """Тест получения каналов с фильтрами."""
        # Вызов API с фильтрами
        response = client.get(
            f"/api/themes/{sample_theme_data['slug']}/channels",
            params={
                'min_subscribers': 50000,
                'min_er': 3.0,
                'limit': 20
            }
        )
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert 'channels' in data
        # Context7: Проверяем, что все каналы соответствуют фильтрам
        for channel in data['channels']:
            assert channel['subscribers'] >= 50000
            if channel.get('er') is not None:
                assert channel['er'] >= 3.0
    
    def test_get_theme_channels_not_found(self, client):
        """Тест получения каналов несуществующей темы."""
        # Вызов API с несуществующим slug
        response = client.get("/api/themes/nonexistent_theme_12345/channels")
        
        # Проверки
        assert response.status_code == 200  # Возвращает пустой список, не 404
        data = response.json()
        assert data['total'] == 0
        assert len(data['channels']) == 0
    
    def test_get_recommended_channels_success(self, client, sample_theme_data, sample_channel_data):
        """Тест успешного получения рекомендованных каналов."""
        # Вызов API
        response = client.get(f"/api/themes/{sample_theme_data['slug']}/recommendations?limit=10")
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Context7: Проверяем структуру канала
        if data:
            channel = data[0]
            assert 'channel_username' in channel
            assert 'title' in channel
            assert 'subscribers' in channel
            assert 'rank_in_theme' in channel
    
    def test_get_recommended_channels_with_filters(self, client, sample_theme_data, sample_channel_data):
        """Тест получения рекомендованных каналов с фильтрами."""
        # Вызов API с фильтрами
        response = client.get(
            f"/api/themes/{sample_theme_data['slug']}/recommendations",
            params={
                'limit': 5,
                'min_subscribers': 50000,
                'min_er': 3.0
            }
        )
        
        # Проверки
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 5
        # Context7: Проверяем фильтры
        for channel in data:
            assert channel['subscribers'] >= 50000
            if channel.get('er') is not None:
                assert channel['er'] >= 3.0
    
    def test_pagination(self, client, sample_theme_data):
        """Тест пагинации списка тем."""
        # Context7: Получаем первую страницу
        response1 = client.get("/api/themes?limit=1&offset=0")
        assert response1.status_code == 200
        data1 = response1.json()
        
        # Context7: Получаем вторую страницу
        response2 = client.get("/api/themes?limit=1&offset=1")
        assert response2.status_code == 200
        data2 = response2.json()
        
        # Context7: Проверяем, что результаты разные (если есть больше одной темы)
        if data1['total'] > 1:
            assert len(data1['themes']) == 1
            assert len(data2['themes']) == 1
            if data1['themes'] and data2['themes']:
                assert data1['themes'][0]['id'] != data2['themes'][0]['id']
