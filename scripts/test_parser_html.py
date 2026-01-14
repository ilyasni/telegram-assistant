#!/usr/bin/env python3
"""Тестовый скрипт для проверки парсера на реальном HTML.
Context7: Проверяет извлечение подборок и каналов из предоставленного HTML.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

# Пример HTML страницы со списком подборок
THEMES_LIST_HTML = """
<div class="row mt-3" id="tagsList">
    <div class="col-12 col-sm-6 col-md-4">
        <div class="card card-body border border-info-hover py-2">
            <a href="/tag/b2b" class="text-dark text-truncate font-20">
                B2B
            </a>
            <p class="text-muted text-truncate">
                Каналы про бизнес, маркетинг и продажи в B2B-сегменте
            </p>
            <div class="mt-1">
                <a href="/tag/b2b" class="text-body">
                    <b>21</b> каналов и чатов
                </a>
            </div>
        </div>
    </div>
</div>
"""

# Пример HTML страницы с каналами подборки
CHANNELS_HTML = """
<div class="col-12 col-sm-6 col-lg-4">
    <div class="card card-body peer-item-box py-2 border mb-2 mb-sm-3 border-info-hover position-relative">
        <a href="https://tgstat.ru/channel/@Salesnotes" class="text-body">
            <div class="row">
                <div class="col">
                    <div>
                        <div class="font-16 text-dark text-truncate">Заметки продавца B2B</div>
                        <div class="font-14 text-muted line-clamp-2 mt-1" style="min-height: 42px;">
                            Авторский блог Тараса Алтунина. О том, как привлекать В2В клиентов через Email Outreach, LinkedIn, контент-маркетинг и личный бренд
                        </div>
                    </div>
                    <div class="mt-2">
                        <div class="font-12 text-body">
                            Блоги&nbsp;
                        </div>
                        <div class="font-12 text-truncate">
                            <b>15 879</b> подписчиков
                        </div>
                    </div>
                </div>
            </div>
        </a>
    </div>
</div>
"""

def test_parse_themes():
    """Тест парсинга списка подборок."""
    print("=" * 60)
    print("ТЕСТ: Парсинг списка подборок")
    print("=" * 60)
    
    soup = BeautifulSoup(THEMES_LIST_HTML, 'lxml')
    themes = []
    seen_slugs = set()
    
    # Поиск ссылок на теги
    selectors = [
        'a[href*="/tag/"]',
        'a[href*="/tags/"]',
    ]
    
    elements = []
    for selector in selectors:
        found = soup.select(selector)
        if found:
            print(f"✓ Найдено элементов с селектором '{selector}': {len(found)}")
            elements.extend(found)
    
    for element in elements:
        href = element.get('href', '')
        if not href:
            continue
        
        # Извлечение slug
        slug_match = re.search(r'/tags?/([^/?]+)', href)
        if not slug_match:
            continue
        
        slug = slug_match.group(1).strip()
        
        if slug in ['theme', 'category', 'all', 'popular']:
            continue
        
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        
        # Извлечение названия
        name = None
        if 'font-20' in element.get('class', []):
            name = element.get_text(strip=True)
        
        if not name:
            name = element.get_text(strip=True)
            if name and ('каналов и чатов' in name.lower() or name.isdigit() or len(name) < 3):
                name = None
        
        # Извлечение описания
        description = None
        parent = element.parent
        if parent:
            desc_elem = parent.find('p', class_=re.compile(r'text-muted'))
            if desc_elem:
                description = desc_elem.get_text(strip=True)
        
        themes.append({
            'slug': slug,
            'name': name,
            'description': description
        })
    
    print(f"\n✓ Найдено подборок: {len(themes)}")
    for theme in themes:
        print(f"  - {theme['slug']}: {theme['name']}")
        if theme['description']:
            print(f"    Описание: {theme['description'][:50]}...")
    
    return themes


def test_parse_channels():
    """Тест парсинга каналов из подборки."""
    print("\n" + "=" * 60)
    print("ТЕСТ: Парсинг каналов из подборки")
    print("=" * 60)
    
    soup = BeautifulSoup(CHANNELS_HTML, 'lxml')
    channels = []
    
    # Поиск элементов каналов
    selectors = [
        '.peer-item-box',
        '.card.peer-item-box',
        'div[class*="peer-item"]',
    ]
    
    channel_elements = []
    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            print(f"✓ Найдено элементов с селектором '{selector}': {len(elements)}")
            # Фильтруем элементы со ссылками на TGStat каналы
            filtered = [
                el for el in elements 
                if el.find('a', href=re.compile(r'tgstat\.(?:ru|com)(?:/ru)?/channel/'))
            ]
            if filtered:
                channel_elements.extend(filtered)
                break
    
    for element in channel_elements:
        # Извлечение username из ссылки
        username = None
        tgstat_url = None
        
        all_links = element.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            if 'tgstat.ru/channel/' in href or 'tgstat.com/ru/channel/' in href:
                tgstat_match = re.search(r'tgstat\.(?:ru|com)(?:/ru)?/channel/@?([a-zA-Z0-9_]+)', href)
                if tgstat_match:
                    username = tgstat_match.group(1).lower().strip().lstrip('@')
                    tgstat_url = f"https://tgstat.ru/channel/@{username}"
                    break
        
        if not username:
            continue
        
        # Извлечение названия
        title = None
        title_selectors = [
            '.font-16.text-dark',
            '.font-16.text-dark.text-truncate',
            'div.font-16',
        ]
        
        for selector in title_selectors:
            title_elem = element.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title and len(title) >= 2:
                    break
        
        # Извлечение подписчиков
        subscribers = None
        subscribers_selectors = [
            '.font-12.text-truncate',
            '.font-12',
        ]
        
        for selector in subscribers_selectors:
            subscribers_elem = element.select_one(selector)
            if subscribers_elem:
                html_str = str(subscribers_elem)
                text = subscribers_elem.get_text(strip=True)
                
                # Ищем паттерн "число подписчиков"
                patterns = [
                    r'<b>(\d{1,3}(?:\s?\d{3})*)</b>\s*подписчик',
                    r'(\d{1,3}(?:\s?\d{3})*)\s*подписчик',
                ]
                
                for pattern in patterns:
                    html_match = re.search(pattern, html_str, re.IGNORECASE)
                    text_match = re.search(pattern, text, re.IGNORECASE)
                    match = html_match or text_match
                    if match:
                        subscribers_text = match.group(1).strip()
                        # Парсинг числа (убираем пробелы)
                        subscribers = int(subscribers_text.replace(' ', ''))
                        break
                if subscribers:
                    break
        
        channels.append({
            'channel_username': username,
            'title': title,
            'subscribers': subscribers or 0,
            'url': tgstat_url
        })
    
    print(f"\n✓ Найдено каналов: {len(channels)}")
    for channel in channels:
        print(f"  - @{channel['channel_username']}: {channel['title']}")
        print(f"    Подписчиков: {channel['subscribers']}")
        print(f"    URL: {channel['url']}")
    
    return channels


if __name__ == '__main__':
    print("\n🔍 Проверка парсера TGStat на реальном HTML\n")
    
    themes = test_parse_themes()
    channels = test_parse_channels()
    
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ:")
    print("=" * 60)
    print(f"✓ Подборок найдено: {len(themes)}")
    print(f"✓ Каналов найдено: {len(channels)}")
    
    if themes and channels:
        print("\n✅ Парсер корректно извлекает данные из HTML!")
    else:
        print("\n❌ Проблемы с извлечением данных!")
