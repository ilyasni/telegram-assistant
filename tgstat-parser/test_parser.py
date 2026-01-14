#!/usr/bin/env python3
"""Тестовый скрипт для проверки парсера TGStat.
Context7: Позволяет протестировать парсер локально перед деплоем.
"""

import sys
import os

# Context7: Добавляем путь к модулям
sys.path.insert(0, os.path.dirname(__file__))

from parser.tgstat_parser import TgStatParser
from parser.quality_checker import QualityChecker
import structlog

# Context7: Настройка логирования для теста
structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
)

logger = structlog.get_logger()


def test_parse_themes():
    """Тест парсинга списка тем."""
    logger.info("Testing themes list parsing...")
    
    parser = TgStatParser()
    themes = parser.parse_themes_list()
    
    logger.info(f"Found {len(themes)} themes")
    for theme in themes[:5]:  # Показываем первые 5
        logger.info(f"  - {theme['slug']}: {theme['name']}")
    
    return themes


def test_parse_theme_channels(theme_slug: str = None):
    """Тест парсинга каналов темы."""
    if not theme_slug:
        # Context7: Используем популярную тему для теста
        theme_slug = "technology"  # Можно изменить на любую другую
    
    logger.info(f"Testing channel parsing for theme: {theme_slug}")
    
    parser = TgStatParser()
    channels = parser.parse_theme_channels(theme_slug)
    
    logger.info(f"Found {len(channels)} channels")
    for i, channel in enumerate(channels[:10], 1):  # Показываем первые 10
        logger.info(
            f"  {i}. @{channel['channel_username']}: "
            f"{channel['title']} - {channel['subscribers']:,} подписчиков"
        )
    
    # Context7: Проверка качества данных
    quality_result = QualityChecker.check_quality_constraints(channels)
    logger.info("Quality check result", **quality_result)
    
    return channels


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test TGStat parser")
    parser.add_argument(
        "--themes",
        action="store_true",
        help="Test themes list parsing"
    )
    parser.add_argument(
        "--channels",
        type=str,
        metavar="THEME_SLUG",
        help="Test channel parsing for specific theme"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Test both themes and channels"
    )
    
    args = parser.parse_args()
    
    if args.all or args.themes:
        test_parse_themes()
    
    if args.all or args.channels:
        test_parse_theme_channels(args.channels)
    
    if not (args.all or args.themes or args.channels):
        parser.print_help()
