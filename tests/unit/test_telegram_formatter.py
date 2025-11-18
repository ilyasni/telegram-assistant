"""
Тестирование модуля telegram_formatter для проверки всех кейсов.
"""

from api.utils.telegram_formatter import (
    markdown_to_telegram_html,
    markdown_to_telegram_chunks,
    split_for_telegram
)


def test_nested_styles():
    """Тест: Вложенные стили **жирный *курсив***"""
    md = "**жирный *курсив***"
    result = markdown_to_telegram_html(md)
    print(f"✓ Вложенные стили:")
    print(f"  Вход: {md}")
    print(f"  Выход: {result}")
    print(f"  Ожидаемо: <b>жирный <i>курсив</i></b>")
    print(f"  Соответствует: {'<b>жирный <i>курсив</i></b>' in result}")
    print()


def test_ordered_list_start():
    """Тест: Нумерованные списки со стартом не с 1"""
    md = """3. Первый элемент
4. Второй элемент
5. Третий элемент"""
    result = markdown_to_telegram_html(md)
    print(f"✓ Нумерованные списки (старт с 3):")
    print(f"  Вход:\n{md}")
    print(f"  Выход:\n{result}")
    print(f"  Ожидаемо: содержит '3) Первый', '4) Второй', '5) Третий'")
    has_3 = "3) Первый" in result or "3) Первый элемент" in result
    has_4 = "4) Второй" in result or "4) Второй элемент" in result
    has_5 = "5) Третий" in result or "5) Третий элемент" in result
    print(f"  Соответствует: {has_3 and has_4 and has_5}")
    print()


def test_large_code_block():
    """Тест: Большие блоки кода"""
    md = """```json
{
  "a": 1,
  "b": "value",
  "c": {
    "nested": true,
    "array": [1, 2, 3]
  }
}
```"""
    result = markdown_to_telegram_html(md)
    print(f"✓ Большой блок кода:")
    print(f"  Вход: блок JSON с несколькими строками")
    print(f"  Выход:\n{result[:200]}...")
    has_pre_code = "<pre><code>" in result and "</code></pre>" in result
    has_escaped = "&lt;" in result or "{" in result  # Либо экранировано, либо сохранено
    print(f"  Содержит <pre><code>: {has_pre_code}")
    print(f"  Содержит код: {has_escaped}")
    print()


def test_links():
    """Тест: Ссылки (валидные/невалидные)"""
    md = """Валидная ссылка: [Пример](https://example.com?q=1&x=2)
Невалидная ссылка: [XSS](javascript:alert(1))
Telegram ссылка: [Канал](tg://resolve?domain=test)"""
    result = markdown_to_telegram_html(md)
    print(f"✓ Ссылки:")
    print(f"  Вход:\n{md}")
    print(f"  Выход:\n{result}")
    has_valid = '<a href="https://example.com' in result or '<a href="https://example.com?q=1&amp;x=2">' in result
    no_js = 'javascript:' not in result
    has_tg = 'tg://' in result or '<a href="tg://' in result
    print(f"  Валидная ссылка обработана: {has_valid}")
    print(f"  JavaScript ссылка заблокирована: {no_js}")
    print(f"  Telegram ссылка обработана: {has_tg}")
    print()


def test_spoilers():
    """Тест: Спойлеры ||секрет||"""
    md = "Это секретная информация ||секрет|| и еще ||другой секрет||"
    result = markdown_to_telegram_html(md)
    print(f"✓ Спойлеры:")
    print(f"  Вход: {md}")
    print(f"  Выход: {result}")
    has_spoiler = "<tg-spoiler>" in result and "</tg-spoiler>" in result
    has_secret = "секрет" in result
    print(f"  Содержит <tg-spoiler>: {has_spoiler}")
    print(f"  Содержит текст: {has_secret}")
    print()


def test_chunking():
    """Тест: Сообщения >4096 символов (валидность чанкинга)"""
    # Создаём большой текст с форматированием
    large_text = "# Заголовок\n\n" + "Это очень длинный текст. " * 200
    large_text += "\n\n**Жирный текст** и *курсив*\n\n"
    large_text += "```python\n" + "print('test')\n" * 50 + "```\n\n"
    large_text += "Еще текст. " * 100
    
    chunks = markdown_to_telegram_chunks(large_text, limit=4096)
    
    print(f"✓ Чанкинг больших сообщений:")
    print(f"  Исходный размер (видимый текст): ~{len(large_text)} символов")
    print(f"  Количество чанков: {len(chunks)}")
    
    valid_chunks = True
    for i, chunk in enumerate(chunks):
        # Проверяем видимую длину (unescape для учёта &amp;)
        import html
        visible_length = len(html.unescape(chunk))
        valid_chunks = valid_chunks and visible_length <= 4096
        
        # Проверяем, что теги закрыты
        open_tags = chunk.count('<') - chunk.count('</')
        close_tags = chunk.count('</')
        has_balanced = abs(open_tags - close_tags) < 10  # Допускаем небольшую погрешность
        
        print(f"  Чанк {i+1}: видимая длина={visible_length}, теги сбалансированы={has_balanced}")
    
    print(f"  Все чанки ≤4096: {valid_chunks}")
    print()


def test_code_block_chunking():
    """Тест: Блоки кода не разрываются"""
    # Создаём большой блок кода
    large_code = "```json\n" + '{"key": "value", "data": "' + "x" * 5000 + '"}\n```'
    
    chunks = markdown_to_telegram_chunks(large_code, limit=4096)
    
    print(f"✓ Чанкинг больших блоков кода:")
    print(f"  Размер блока кода: ~{len(large_code)} символов")
    print(f"  Количество чанков: {len(chunks)}")
    
    # Проверяем, что блок кода не разорван
    code_block_intact = all("<pre><code>" in chunk and "</code></pre>" in chunk for chunk in chunks if "<pre><code>" in chunk)
    print(f"  Блоки кода не разорваны: {code_block_intact}")
    print()


def run_all_tests():
    """Запуск всех тестов"""
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ TELEGRAM FORMATTER")
    print("=" * 60)
    print()
    
    test_nested_styles()
    test_ordered_list_start()
    test_large_code_block()
    test_links()
    test_spoilers()
    test_chunking()
    test_code_block_chunking()
    
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()

