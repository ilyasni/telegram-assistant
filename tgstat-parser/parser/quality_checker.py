"""Проверка качества данных после парсинга.
Context7: Валидация, сортировка и проверка лимитов для обеспечения качества данных.
"""

from typing import List, Dict, Any, Tuple
import structlog
import re

logger = structlog.get_logger()


class QualityChecker:
    """Проверка качества данных каналов.
    
    Context7: Реализует валидацию, сортировку и проверку лимитов.
    """
    
    @staticmethod
    def validate_and_sort_channels(
        channels: List[Dict[str, Any]],
        max_channels: int = 30
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Валидация и сортировка каналов.
        
        Context7: Проверяет все обязательные поля, сортирует по подписчикам,
        ограничивает количество и возвращает список ошибок валидации.
        
        Args:
            channels: Список словарей с данными каналов
            max_channels: Максимальное количество каналов (Context7: по умолчанию 30)
            
        Returns:
            Tuple (валидные каналы, список ошибок валидации)
        """
        errors = []
        valid_channels = []
        
        for idx, channel in enumerate(channels):
            channel_errors = QualityChecker._validate_channel(channel, idx)
            
            if channel_errors:
                errors.extend(channel_errors)
                logger.warning(
                    "Channel validation failed",
                    channel_index=idx,
                    errors=channel_errors,
                    channel_data=channel
                )
            else:
                valid_channels.append(channel)
        
        # Context7: Сортировка по числу подписчиков (по убыванию)
        valid_channels.sort(key=lambda x: x.get('subscribers', 0), reverse=True)
        
        # Context7: Ограничение до max_channels
        if len(valid_channels) > max_channels:
            logger.info(
                "Channels limited to max_channels",
                total_channels=len(valid_channels),
                max_channels=max_channels
            )
            valid_channels = valid_channels[:max_channels]
        
        # Context7: Установка рангов после сортировки и ограничения
        for rank, channel in enumerate(valid_channels, start=1):
            channel['rank_in_theme'] = rank
        
        logger.info(
            "Channels validated and sorted",
            total_channels=len(channels),
            valid_channels=len(valid_channels),
            errors_count=len(errors)
        )
        
        return valid_channels, errors
    
    @staticmethod
    def _validate_channel(channel: Dict[str, Any], index: int) -> List[str]:
        """Валидация одного канала.
        
        Context7: Проверяет все обязательные поля и их корректность.
        
        Args:
            channel: Словарь с данными канала
            index: Индекс канала в списке (для логирования)
            
        Returns:
            Список ошибок валидации (пустой если канал валиден)
        """
        errors = []
        
        # Context7: Проверка обязательных полей (subscribers может быть 0)
        required_fields = ['channel_username', 'title', 'url']
        for field in required_fields:
            if field not in channel or not channel[field]:
                errors.append(f"Missing or empty field: {field}")
        
        # Context7: subscribers проверяется отдельно (может быть 0)
        
        if errors:
            return errors
        
        # Context7: Валидация username
        username = channel.get('channel_username', '')
        if not username or len(username) < 1:
            errors.append("Invalid username: empty or too short")
        elif not re.match(r'^[a-z0-9_]+$', username.lower()):
            errors.append(f"Invalid username format: {username}")
        
        # Context7: Валидация title
        title = channel.get('title', '')
        if not title or len(title) < 1:
            errors.append("Invalid title: empty")
        elif len(title) > 500:
            errors.append(f"Invalid title: too long ({len(title)} > 500)")
        
        # Context7: Валидация subscribers (разрешаем 0, так как данные могут быть недоступны)
        subscribers = channel.get('subscribers')
        if subscribers is None:
            # Context7: Если subscribers не указан, устанавливаем 0
            channel['subscribers'] = 0
            subscribers = 0
        elif not isinstance(subscribers, int):
            errors.append(f"Invalid subscribers type: {type(subscribers)}")
        elif subscribers < 0:
            errors.append(f"Invalid subscribers value: {subscribers} < 0")
        
        # Context7: Валидация ER (опционально)
        er = channel.get('er')
        if er is not None:
            if not isinstance(er, (int, float)):
                errors.append(f"Invalid ER type: {type(er)}")
            elif er < 0 or er > 100:
                errors.append(f"Invalid ER value: {er} not in [0, 100]")
        
        # Context7: Валидация URL (разрешаем TGStat ссылки)
        url = channel.get('url', '')
        if not url:
            errors.append("Missing URL")
        elif url.startswith('https://tgstat.ru/channel/'):
            # Context7: Проверка соответствия username и TGStat URL
            tgstat_match = re.search(r'tgstat\.ru/channel/@?([a-zA-Z0-9_]+)', url)
            if tgstat_match:
                username_from_url = tgstat_match.group(1).lower()
                if username_from_url != username.lower():
                    errors.append(
                        f"Username mismatch: TGStat URL username '{username_from_url}' != channel username '{username}'"
                    )
            else:
                errors.append(f"Invalid TGStat URL format: {url}")
        elif url.startswith('https://t.me/'):
            # Context7: Проверка соответствия username и Telegram URL
            username_from_url = url.replace('https://t.me/', '').split('/')[0]
            if username_from_url.lower() != username.lower():
                errors.append(
                    f"Username mismatch: URL username '{username_from_url}' != channel username '{username}'"
                )
        else:
            errors.append(f"Invalid URL format: {url} (must start with https://tgstat.ru/channel/ or https://t.me/)")
        
        return errors
    
    @staticmethod
    def check_quality_constraints(channels: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Проверка ограничений качества данных.
        
        Context7: Проверяет, что данные соответствуют всем требованиям качества.
        
        Args:
            channels: Список валидных каналов
            
        Returns:
            Словарь с результатами проверки
        """
        result = {
            'valid': True,
            'channels_count': len(channels),
            'max_channels_exceeded': len(channels) > 30,
            'sorted_by_subscribers': True,
            'all_fields_present': True,
            'errors': []
        }
        
        # Context7: Проверка количества каналов
        if len(channels) > 30:
            result['valid'] = False
            result['errors'].append(f"Too many channels: {len(channels)} > 30")
        
        # Context7: Проверка сортировки по подписчикам
        for i in range(len(channels) - 1):
            if channels[i].get('subscribers', 0) < channels[i + 1].get('subscribers', 0):
                result['valid'] = False
                result['sorted_by_subscribers'] = False
                result['errors'].append("Channels not sorted by subscribers (descending)")
                break
        
        # Context7: Проверка наличия всех обязательных полей
        required_fields = ['channel_username', 'title', 'subscribers', 'url', 'rank_in_theme']
        for idx, channel in enumerate(channels):
            missing_fields = [field for field in required_fields if field not in channel]
            if missing_fields:
                result['valid'] = False
                result['all_fields_present'] = False
                result['errors'].append(
                    f"Channel {idx} missing fields: {', '.join(missing_fields)}"
                )
        
        # Context7: Проверка уникальности username в списке
        usernames = [ch.get('channel_username') for ch in channels]
        if len(usernames) != len(set(usernames)):
            result['valid'] = False
            result['errors'].append("Duplicate usernames in channels list")
        
        return result
