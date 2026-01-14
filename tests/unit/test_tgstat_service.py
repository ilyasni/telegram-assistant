"""
Unit тесты для TGStat Service.
Context7: Тестирование бизнес-логики работы с темами и каналами TGStat.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from uuid import uuid4

from api.services.tgstat_service import TGStatService, get_tgstat_service


class TestTGStatService:
    """Тесты для TGStatService."""
    
    @pytest.fixture
    def service(self):
        """Фикстура для создания экземпляра сервиса."""
        return TGStatService()
    
    @pytest.fixture
    def mock_db(self):
        """Фикстура для создания mock сессии БД."""
        db = Mock()
        return db
    
    @pytest.fixture
    def sample_theme_data(self):
        """Фикстура с примерными данными темы."""
        return {
            'id': str(uuid4()),
            'slug': 'technology',
            'name': 'Technology',
            'description': 'Technology channels',
            'channels_count': 30,
            'indexed_at': datetime.now(timezone.utc),
            'created_at': datetime.now(timezone.utc)
        }
    
    @pytest.fixture
    def sample_channel_data(self):
        """Фикстура с примерными данными канала."""
        return {
            'id': str(uuid4()),
            'channel_username': 'test_channel',
            'title': 'Test Channel',
            'subscribers': 100000,
            'er': 5.5,
            'url': 'https://t.me/test_channel',
            'rank_in_theme': 1,
            'indexed_at': datetime.now(timezone.utc)
        }
    
    def test_get_all_themes_success(self, service, mock_db, sample_theme_data):
        """Тест успешного получения списка тем."""
        # Context7: Настройка mock для запроса
        mock_result = Mock()
        mock_row = Mock()
        mock_row.id = uuid4()
        mock_row.slug = sample_theme_data['slug']
        mock_row.name = sample_theme_data['name']
        mock_row.description = sample_theme_data['description']
        mock_row.channels_count = sample_theme_data['channels_count']
        mock_row.indexed_at = sample_theme_data['indexed_at']
        mock_row.created_at = sample_theme_data['created_at']
        
        mock_result.__iter__ = Mock(return_value=iter([mock_row]))
        mock_db.execute.return_value = mock_result
        
        # Context7: Mock для count запроса
        mock_count_result = Mock()
        mock_count_result.scalar.return_value = 1
        mock_db.execute.side_effect = [mock_count_result, mock_result]
        
        # Вызов метода
        result = service.get_all_themes(db=mock_db, limit=10, offset=0)
        
        # Проверки
        assert result['total'] == 1
        assert len(result['themes']) == 1
        assert result['themes'][0]['slug'] == sample_theme_data['slug']
        assert result['limit'] == 10
        assert result['offset'] == 0
    
    def test_get_all_themes_with_search(self, service, mock_db, sample_theme_data):
        """Тест получения тем с поиском."""
        # Context7: Настройка mock
        mock_result = Mock()
        mock_row = Mock()
        mock_row.id = uuid4()
        mock_row.slug = sample_theme_data['slug']
        mock_row.name = sample_theme_data['name']
        mock_row.description = sample_theme_data['description']
        mock_row.channels_count = sample_theme_data['channels_count']
        mock_row.indexed_at = sample_theme_data['indexed_at']
        mock_row.created_at = sample_theme_data['created_at']
        
        mock_result.__iter__ = Mock(return_value=iter([mock_row]))
        
        mock_count_result = Mock()
        mock_count_result.scalar.return_value = 1
        mock_db.execute.side_effect = [mock_count_result, mock_result]
        
        # Вызов метода с поиском
        result = service.get_all_themes(db=mock_db, limit=10, offset=0, search='tech')
        
        # Проверки
        assert result['total'] == 1
        assert len(result['themes']) == 1
    
    def test_get_theme_by_slug_success(self, service, mock_db, sample_theme_data):
        """Тест успешного получения темы по slug."""
        # Context7: Настройка mock
        mock_result = Mock()
        mock_row = Mock()
        mock_row.id = uuid4()
        mock_row.slug = sample_theme_data['slug']
        mock_row.name = sample_theme_data['name']
        mock_row.description = sample_theme_data['description']
        mock_row.channels_count = sample_theme_data['channels_count']
        mock_row.indexed_at = sample_theme_data['indexed_at']
        mock_row.created_at = sample_theme_data['created_at']
        
        mock_result.first.return_value = mock_row
        mock_db.execute.return_value = mock_result
        
        # Вызов метода
        result = service.get_theme_by_slug(db=mock_db, slug='technology')
        
        # Проверки
        assert result is not None
        assert result['slug'] == sample_theme_data['slug']
        assert result['name'] == sample_theme_data['name']
    
    def test_get_theme_by_slug_not_found(self, service, mock_db):
        """Тест получения несуществующей темы."""
        # Context7: Настройка mock для возврата None
        mock_result = Mock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result
        
        # Вызов метода
        result = service.get_theme_by_slug(db=mock_db, slug='nonexistent')
        
        # Проверки
        assert result is None
    
    def test_get_theme_channels_success(self, service, mock_db, sample_theme_data, sample_channel_data):
        """Тест успешного получения каналов темы."""
        # Context7: Настройка mock для темы
        mock_theme_result = Mock()
        mock_theme_row = Mock()
        mock_theme_row.id = uuid4()
        mock_theme_row.slug = sample_theme_data['slug']
        mock_theme_row.name = sample_theme_data['name']
        mock_theme_row.description = sample_theme_data['description']
        mock_theme_row.channels_count = sample_theme_data['channels_count']
        mock_theme_row.indexed_at = sample_theme_data['indexed_at']
        mock_theme_row.created_at = sample_theme_data['created_at']
        
        mock_theme_result.first.return_value = mock_theme_row
        
        # Context7: Настройка mock для каналов
        mock_channels_result = Mock()
        mock_channel_row = Mock()
        mock_channel_row.id = uuid4()
        mock_channel_row.channel_username = sample_channel_data['channel_username']
        mock_channel_row.title = sample_channel_data['title']
        mock_channel_row.subscribers = sample_channel_data['subscribers']
        mock_channel_row.er = sample_channel_data['er']
        mock_channel_row.url = sample_channel_data['url']
        mock_channel_row.rank_in_theme = sample_channel_data['rank_in_theme']
        mock_channel_row.indexed_at = sample_channel_data['indexed_at']
        
        mock_channels_result.__iter__ = Mock(return_value=iter([mock_channel_row]))
        
        # Context7: Настройка mock для count
        mock_count_result = Mock()
        mock_count_result.scalar.return_value = 1
        
        # Context7: Настройка side_effect для последовательных вызовов
        mock_db.execute.side_effect = [
            mock_theme_result,  # get_theme_by_slug
            mock_count_result,  # count query
            mock_channels_result  # channels query
        ]
        
        # Вызов метода
        result = service.get_theme_channels(db=mock_db, theme_slug='technology')
        
        # Проверки
        assert result['total'] == 1
        assert len(result['channels']) == 1
        assert result['channels'][0]['channel_username'] == sample_channel_data['channel_username']
        assert result['theme_slug'] == 'technology'
    
    def test_get_theme_channels_not_found(self, service, mock_db):
        """Тест получения каналов несуществующей темы."""
        # Context7: Настройка mock для возврата None (тема не найдена)
        mock_result = Mock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result
        
        # Вызов метода
        result = service.get_theme_channels(db=mock_db, theme_slug='nonexistent')
        
        # Проверки
        assert result['total'] == 0
        assert len(result['channels']) == 0
        assert result['theme_slug'] == 'nonexistent'
    
    def test_get_recommended_channels(self, service, mock_db, sample_theme_data, sample_channel_data):
        """Тест получения рекомендованных каналов."""
        # Context7: Используем существующий метод get_theme_channels
        with patch.object(service, 'get_theme_channels') as mock_get_channels:
            mock_get_channels.return_value = {
                'channels': [sample_channel_data],
                'total': 1,
                'limit': 10,
                'offset': 0,
                'theme_slug': 'technology'
            }
            
            # Вызов метода
            result = service.get_recommended_channels(
                db=mock_db,
                theme_slug='technology',
                limit=10
            )
            
            # Проверки
            assert len(result) == 1
            assert result[0]['channel_username'] == sample_channel_data['channel_username']
            mock_get_channels.assert_called_once_with(
                db=mock_db,
                theme_slug='technology',
                limit=10,
                offset=0,
                min_subscribers=None,
                min_er=None
            )
    
    def test_get_tgstat_service_singleton(self):
        """Тест singleton pattern для get_tgstat_service."""
        # Context7: Проверка, что возвращается один и тот же экземпляр
        service1 = get_tgstat_service()
        service2 = get_tgstat_service()
        
        assert service1 is service2
        assert isinstance(service1, TGStatService)
