"""
Конвертация Markdown в Telegram HTML формат.

Модуль предоставляет функции для конвертации Markdown текста в валидный Telegram HTML
с whitelist-тегами, URL-санитизацией и безопасным чанкингом под лимит 4096 символов.
"""

import html
import re
from typing import Optional, List, Tuple
from urllib.parse import urlparse

import mistune
from mistune.renderers.html import HTMLRenderer
from mistune.util import escape


class TelegramHTMLRenderer(HTMLRenderer):
    """Кастомный renderer для конвертации Markdown в Telegram HTML."""
    
    # Whitelist разрешённых тегов Telegram
    ALLOWED_TAGS = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'tg-spoiler'}
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def heading(self, text: str, level: int, **attrs) -> str:
        """
        Заголовки с улучшенным форматированием.
        Context7: Добавляем визуальные разделители для лучшей читабельности.
        """
        # Убираем лишние пробелы
        text = text.strip()
        
        # Для заголовков уровня 1 и 2 добавляем разделитель
        if level == 1:
            # H1: большой заголовок с разделителем сверху и снизу
            return f"\n<b>{text}</b>\n━━━━━━━━━━\n\n"
        elif level == 2:
            # H2: средний заголовок с разделителем снизу
            return f"\n<b>{text}</b>\n──────────\n\n"
        else:
            # H3+: обычный заголовок
            return f"\n<b>{text}</b>\n\n"
    
    def paragraph(self, text: str) -> str:
        """
        Абзацы с улучшенным форматированием.
        Context7: Добавляем пустую строку после абзаца для читабельности.
        """
        if not text.strip():
            return "\n"
        # Убираем лишние переносы внутри абзаца
        text = re.sub(r'\n+', ' ', text.strip())
        return f"{text}\n\n"
    
    def list(self, text: str, ordered: bool, **attrs) -> str:
        """
        Списки с улучшенным форматированием.
        Context7: Добавляем отступы и улучшенные маркеры для читабельности.
        """
        # Получаем start из attrs если есть
        start = attrs.get('start', 1)
        
        # Находим все <li> элементы (с сохранением вложенного форматирования)
        li_pattern = r'<li[^>]*>(.*?)</li>'
        matches = list(re.finditer(li_pattern, text, re.DOTALL))
        
        if not matches:
            return text
        
        result_parts = []
        last_end = 0
        
        for idx, match in enumerate(matches):
            # Сохраняем текст до текущего <li>
            if match.start() > last_end:
                before_text = text[last_end:match.start()]
                result_parts.append(before_text)
            
            # Извлекаем содержимое <li> с сохранением форматирования
            item_content = match.group(1).strip()
            
            # Определяем маркер с улучшенным форматированием
            if ordered:
                marker = f"<b>{start + idx}.</b> "
            else:
                marker = "▸ "  # Более заметный маркер для неупорядоченных списков
            
            # Добавляем маркер и содержимое (с сохранением HTML форматирования)
            result_parts.append(f"{marker}{item_content}\n")
            
            last_end = match.end()
        
        # Добавляем оставшийся текст после последнего </li>
        if last_end < len(text):
            result_parts.append(text[last_end:])
        
        # Добавляем пустую строку после списка для читабельности
        result = "".join(result_parts)
        if result.strip():
            result += "\n"
        
        return result
    
    def list_item(self, text: str, **attrs) -> str:
        """
        Элемент списка: оборачиваем в <li> для последующей обработки в list().
        В mistune v3 list_item() вызывается ДО list(), поэтому оборачиваем в тег.
        """
        # Оборачиваем в <li> для последующей обработки в list()
        return f"<li>{text}</li>"
    
    def block_code(self, code: str, info: Optional[str] = None) -> str:
        """Блок кода → <pre><code>...</code></pre> (только escape, без атрибутов)"""
        escaped_code = escape(code)
        return f"<pre><code>{escaped_code}</code></pre>\n"
    
    def codespan(self, text: str) -> str:
        """Инлайн-код → <code>...</code>"""
        escaped_text = escape(text)
        return f"<code>{escaped_text}</code>"
    
    def emphasis(self, text: str) -> str:
        """Курсив *text* → <i>...</i>"""
        return f"<i>{text}</i>"
    
    def strong(self, text: str) -> str:
        """Жирный **text** → <b>...</b>"""
        return f"<b>{text}</b>"
    
    def link(self, text: str, url: str, title: Optional[str] = None) -> str:
        """Ссылки → <a href="url">text</a> (только валидные URL)"""
        sanitized_url = _sanitize_url(url)
        if sanitized_url:
            escaped_text = escape(text)
            return f'<a href="{sanitized_url}">{escaped_text}</a>'
        return text  # Если URL невалидный, возвращаем только текст
    
    def image(self, alt: str, url: str, title: Optional[str] = None) -> str:
        """Изображения → текст alt + (опционально) ссылка"""
        result = alt if alt else "[изображение]"
        sanitized_url = _sanitize_url(url)
        if sanitized_url:
            result += f' <a href="{sanitized_url}">[изображение]</a>'
        return result
    
    def strikethrough(self, text: str) -> str:
        """Зачёркнутый текст → <s>...</s>"""
        return f"<s>{text}</s>"
    
    def block_quote(self, text: str) -> str:
        """
        Цитаты с улучшенным форматированием.
        Context7: Добавляем визуальное выделение цитат для читабельности.
        """
        # Убираем лишние переносы и добавляем префикс
        lines = text.strip().split('\n')
        quoted_lines = []
        for line in lines:
            if line.strip():
                # Добавляем визуальный маркер цитаты
                quoted_lines.append(f"│ {line.strip()}")
            else:
                quoted_lines.append("")
        
        # Обёртываем в курсив для визуального выделения
        result = "\n".join(quoted_lines)
        if result.strip():
            return f"<i>{result}</i>\n\n"
        return "\n"
    
    def table(self, text: str) -> str:
        """Таблицы → упрощение до <pre> или текста"""
        # Упрощаем таблицы до текстового формата в <pre>
        return f"<pre>{escape(text)}</pre>\n"
    
    def thematic_break(self) -> str:
        """
        Горизонтальная линия с улучшенным форматированием.
        Context7: Добавляем визуальный разделитель для лучшей читабельности.
        """
        return "\n━━━━━━━━━━\n\n"
    
    def blank_line(self) -> str:
        """Пустая строка"""
        return "\n"


def _sanitize_url(url: str) -> Optional[str]:
    """
    Валидация и санитизация URL для Telegram.
    
    Разрешены только: http:, https:, tg:
    Запрещены: javascript:, data:, пустые, относительные без протокола.
    """
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()
    if not url:
        return None
    
    # Нормализация пробелов и невидимых символов
    url = re.sub(r'[\s\x00-\x1f\x7f-\x9f]+', '', url)
    
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        
        # Разрешены только http, https, tg
        if scheme not in ('http', 'https', 'tg'):
            return None
        
        # Запрещаем javascript: и data: (на случай если они попали в path)
        if 'javascript:' in url.lower() or 'data:' in url.lower():
            return None
        
        # Восстанавливаем URL с валидной схемой
        if not parsed.netloc and scheme == 'tg':
            # Для tg:// ссылок может не быть netloc
            full_url = url
        else:
            full_url = f"{scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                full_url += f"?{parsed.query}"
            if parsed.fragment:
                full_url += f"#{parsed.fragment}"
        
        # Escape в href: & → &amp;, " → &quot;
        full_url = full_url.replace('&', '&amp;').replace('"', '&quot;')
        
        return full_url
    
    except Exception:
        return None


def split_for_telegram(html_text: str, limit: int = 4096) -> List[str]:
    """
    Безопасный чанкинг HTML для Telegram с сохранением валидности тегов.
    
    Подсчитывает видимый текст (игнорируя теги), html.unescape для учёта
    &amp; → 1 символ. Блоки <pre><code>...</code></pre> не разрываются.
    """
    if not html_text:
        return [""]
    
    # Токенизация HTML на текст и теги
    tokens = _tokenize_html(html_text)
    
    chunks = []
    current_chunk = []
    current_length = 0
    tag_stack = []  # Стек открытых тегов
    
    i = 0
    while i < len(tokens):
        token_type, token_value = tokens[i]
        
        if token_type == 'text':
            # Подсчитываем видимую длину (unescape для &amp; → 1 символ)
            visible_text = html.unescape(token_value)
            visible_length = len(visible_text)
            
            if current_length + visible_length > limit:
                # Переполнение: закрываем текущий чанк
                if current_chunk:
                    # Закрываем все открытые теги
                    closed_chunk = _close_chunk(current_chunk, tag_stack)
                    chunks.append(closed_chunk)
                
                # Начинаем новый чанк с переоткрытием тегов
                current_chunk = _reopen_tags(tag_stack)
                current_length = sum(len(html.unescape(v)) for t, v in current_chunk if t == 'text')
                
                # Добавляем текст в новый чанк
                current_chunk.append(('text', token_value))
                current_length += visible_length
            
            else:
                current_chunk.append(('text', token_value))
                current_length += visible_length
        
        elif token_type == 'open_tag':
            tag_name = _extract_tag_name(token_value)
            if tag_name in TelegramHTMLRenderer.ALLOWED_TAGS:
                current_chunk.append(('open_tag', token_value))
                tag_stack.append(tag_name)
        
        elif token_type == 'close_tag':
            tag_name = _extract_tag_name(token_value)
            if tag_name in TelegramHTMLRenderer.ALLOWED_TAGS:
                current_chunk.append(('close_tag', token_value))
                if tag_stack and tag_stack[-1] == tag_name:
                    tag_stack.pop()
        
        elif token_type == 'self_tag':
            # Самозакрывающиеся теги не добавляются в стек
            current_chunk.append(('self_tag', token_value))
        
        elif token_type == 'code_block':
            # Атомарный блок кода: не разрываем
            code_block_text = token_value
            code_block_visible = html.unescape(code_block_text)
            code_block_length = len(code_block_visible)
            
            if code_block_length > limit:
                # Блок больше лимита - отдельным сообщением
                if current_chunk:
                    closed_chunk = _close_chunk(current_chunk, tag_stack)
                    chunks.append(closed_chunk)
                    current_chunk = []
                    current_length = 0
                
                chunks.append(code_block_text)
                current_chunk = []
                current_length = 0
            
            elif current_length + code_block_length > limit:
                # Не помещается в текущий чанк
                if current_chunk:
                    closed_chunk = _close_chunk(current_chunk, tag_stack)
                    chunks.append(closed_chunk)
                
                chunks.append(code_block_text)
                current_chunk = []
                current_length = 0
            
            else:
                current_chunk.append(('code_block', code_block_text))
                current_length += code_block_length
        
        i += 1
    
    # Добавляем последний чанк
    if current_chunk:
        closed_chunk = _close_chunk(current_chunk, tag_stack)
        chunks.append(closed_chunk)
    
    return chunks if chunks else [""]


def _tokenize_html(html: str) -> List[Tuple[str, str]]:
    """Токенизация HTML на текст, теги и блоки кода."""
    tokens = []
    i = 0
    
    while i < len(html):
        # Поиск блока <pre><code>...</code></pre>
        pre_code_match = re.search(r'<pre><code>(.*?)</code></pre>', html[i:], re.DOTALL)
        if pre_code_match:
            # Добавляем текст до блока
            if pre_code_match.start() > 0:
                text_before = html[i:i + pre_code_match.start()]
                if text_before:
                    tokens.extend(_tokenize_text_and_tags(text_before))
            
            # Добавляем блок кода как атомарный токен
            full_block = html[i + pre_code_match.start():i + pre_code_match.end()]
            tokens.append(('code_block', full_block))
            i += pre_code_match.end()
            continue
        
        # Обычная токенизация
        remaining = html[i:]
        tokens.extend(_tokenize_text_and_tags(remaining))
        break
    
    return tokens


def _tokenize_text_and_tags(html: str) -> List[Tuple[str, str]]:
    """Токенизация текста и HTML тегов."""
    tokens = []
    i = 0
    
    while i < len(html):
        # Ищем открывающий тег
        tag_match = re.match(r'<([^>]+)>', html[i:])
        if tag_match:
            tag_full = html[i:i + tag_match.end()]
            tag_content = tag_match.group(1)
            
            # Проверяем тип тега
            if tag_content.startswith('/'):
                # Закрывающий тег
                tokens.append(('close_tag', tag_full))
            elif tag_content.endswith('/'):
                # Самозакрывающийся тег
                tokens.append(('self_tag', tag_full))
            else:
                # Открывающий тег
                tokens.append(('open_tag', tag_full))
            
            i += tag_match.end()
        else:
            # Текст до следующего тега или до конца
            text_end = html.find('<', i)
            if text_end == -1:
                text = html[i:]
                if text:
                    tokens.append(('text', text))
                break
            else:
                text = html[i:text_end]
                if text:
                    tokens.append(('text', text))
                i = text_end
    
    return tokens


def _extract_tag_name(tag: str) -> str:
    """Извлекает имя тега из HTML тега."""
    match = re.match(r'</?([a-zA-Z0-9-]+)', tag)
    if match:
        return match.group(1).lower()
    return ""


def _close_chunk(chunk: List[Tuple[str, str]], tag_stack: List[str]) -> str:
    """Закрывает чанк, закрывая все открытые теги."""
    result_parts = [value for _, value in chunk]
    
    # Закрываем теги в обратном порядке
    for tag_name in reversed(tag_stack):
        result_parts.append(f"</{tag_name}>")
    
    return "".join(result_parts)


def _reopen_tags(tag_stack: List[str]) -> List[Tuple[str, str]]:
    """Переоткрывает теги для нового чанка."""
    result = []
    for tag_name in tag_stack:
        result.append(('open_tag', f"<{tag_name}>"))
    return result


def _preprocess_spoilers(md_text: str) -> Tuple[str, List[str]]:
    """
    Предобработка спойлеров: конвертирует ||text|| в markdown bold для последующей замены.
    """
    # Заменяем ||text|| на временный маркер, который не конфликтует с markdown
    # Используем специальный символ, который не используется в markdown
    pattern = r'\|\|([^\n\|]+?)\|\|'
    
    spoiler_markers = []
    
    def replace_spoiler(match):
        content = match.group(1)
        marker_id = len(spoiler_markers)
        spoiler_markers.append(content)
        # Используем уникальный маркер, который точно не будет экранирован
        return f'__SPOILER_MARKER_{marker_id}__'
    
    processed_text = re.sub(pattern, replace_spoiler, md_text)
    return processed_text, spoiler_markers


def _postprocess_spoilers(html_text: str, spoiler_markers: List[str]) -> str:
    """
    Постобработка: заменяет временные маркеры спойлеров на <tg-spoiler>.
    """
    # Заменяем маркеры на валидный Telegram тег
    for idx, content in enumerate(spoiler_markers):
        marker = f'__SPOILER_MARKER_{idx}__'
        # Экранируем содержимое спойлера
        escaped_content = escape(content)
        html_text = html_text.replace(marker, f'<tg-spoiler>{escaped_content}</tg-spoiler>')
    
    html_text = _sanitize_telegram_html(html_text)
    
    return html_text


def _sanitize_telegram_html(html_text: str) -> str:
    """
    Context7 best practice: очищает HTML перед отправкой в Telegram.
    
    - Заменяет <br> на перевод строки
    - Удаляет теги, не входящие в whitelist Telegram
    - Нормализует лишние пробелы вокруг переводов
    """
    if not html_text:
        return html_text
    
    # Заменяем <br> / <br/> на перенос строки
    html_text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    
    allowed_tags = TelegramHTMLRenderer.ALLOWED_TAGS
    
    def _strip_disallowed(match: re.Match) -> str:
        tag = match.group(0)
        tag_name = match.group(1).lower()
        if tag_name in allowed_tags:
            return tag
        # Удаляем тег полностью, оставляя содержимое
        return ""
    
    # Удаляем все открывающие/закрывающие теги вне whitelist
    html_text = re.sub(r"</?([a-zA-Z0-9\-]+)(?:\s[^>]*)?>", _strip_disallowed, html_text)
    
    # Приводим к единообразию количество пустых строк
    html_text = re.sub(r"\n{3,}", "\n\n", html_text)
    html_text = html_text.strip()
    
    return html_text


def markdown_to_telegram_html(md_text: str) -> str:
    """
    Конвертирует Markdown в Telegram HTML.
    
    Args:
        md_text: Текст в формате Markdown
        
    Returns:
        Текст в формате Telegram HTML
    """
    if not md_text:
        return ""
    
    # Предобработка спойлеров
    md_text_processed, spoiler_markers = _preprocess_spoilers(md_text)
    
    # Создаём кастомный renderer
    renderer = TelegramHTMLRenderer()
    
    # Создаём markdown парсер с плагинами (strikethrough для ~~text~~)
    markdown = mistune.create_markdown(
        renderer=renderer,
        plugins=['strikethrough']
    )
    
    # Конвертируем
    html_text = markdown(md_text_processed)
    
    # Постобработка спойлеров
    html_text = _postprocess_spoilers(html_text, spoiler_markers)
    
    # Context7: Постобработка для улучшения читабельности
    # Убираем более 2 подряд идущих переносов строк (оставляем максимум 2)
    html_text = re.sub(r'\n{3,}', '\n\n', html_text)
    
    # Убираем переносы в начале и конце
    html_text = html_text.strip()
    
    return html_text


def markdown_to_telegram_chunks(md_text: str, limit: int = 4096) -> List[str]:
    """
    Конвертирует Markdown в Telegram HTML и разбивает на чанки.
    
    Args:
        md_text: Текст в формате Markdown
        limit: Максимальный размер чанка в символах (видимый текст)
        
    Returns:
        Список чанков HTML валидных для Telegram
    """
    html_text = markdown_to_telegram_html(md_text)
    return split_for_telegram(html_text, limit=limit)

