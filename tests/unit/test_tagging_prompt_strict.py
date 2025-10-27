"""Unit-тесты для строгого промпта тегирования."""

import json
import pytest
from worker.prompts.tagging import STRICT_TAGGING_PROMPT


def test_prompt_format_contains_text_placeholder():
    """Промпт содержит placeholder для текста."""
    text = "Газпром нефть открыл хаб в Омске для авиакеросина."
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    assert "Газпром нефть" in prompt
    assert "авиакеросина" in prompt


def test_prompt_contains_strict_rules():
    """Промпт содержит строгие правила тегирования."""
    text = "Тестовый текст"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    # Проверка ключевых правил
    assert "подстрокой исходного текста" in prompt
    assert "мета-тегов" in prompt
    assert "JSON-массив строк" in prompt
    assert "пустой массив []" in prompt
    assert "точное вхождение" in prompt
    assert "без синонимов" in prompt


def test_prompt_forbids_meta_tags():
    """Промпт запрещает мета-теги."""
    text = "Тестовый текст"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    # Проверка запрета мета-тегов
    assert "экономика" in prompt
    assert "инвестиции" in prompt
    assert "финансоваяаналитика" in prompt
    assert "запрещены" in prompt


def test_prompt_requires_exact_substrings():
    """Промпт требует точных подстрок."""
    text = "Тестовый текст"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    assert "точное вхождение" in prompt
    assert "без синонимов и обобщений" in prompt


def test_prompt_output_format():
    """Промпт требует JSON-массив строк."""
    text = "Тестовый текст"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    assert "JSON-массив строк" in prompt
    assert "без дополнительных полей" in prompt
    assert "без комментариев" in prompt


TEST_CASES = [
    {
        "name": "meta_tags_forbidden",
        "text": "ЦБ опубликовал обзор ликвидности банковского сектора за сентябрь. Ставка RUONIA стабилизировалась.",
        "expected": ["ликвидности банковского сектора", "ruonia"],
        "forbidden": ["экономика", "инвестиции", "финансоваяаналитика"]
    },
    {
        "name": "synonyms_forbidden",
        "text": "Газпром нефть открыл новый логистический хаб в Омске для авиакеросина.",
        "expected": ["газпром нефть", "омске", "авиакеросина"],
        "forbidden": ["энергетика", "нефтегаз"]
    },
    {
        "name": "empty_result_allowed",
        "text": "Сегодня стрим в 19:00, разбор бэктестов.",
        "expected": [],
        "forbidden": []
    }
]


@pytest.mark.parametrize("case", TEST_CASES, ids=[c["name"] for c in TEST_CASES])
def test_strict_tagging_rules(case):
    """Проверка правил строгого тегирования."""
    # Здесь нужен мок LLM, который вернёт JSON-массив
    # Для unit-теста проверяем только формат промпта
    prompt = STRICT_TAGGING_PROMPT.format(text=case["text"])
    
    # Проверка, что правила присутствуют в промпте
    assert "подстрокой исходного текста" in prompt
    assert "мета-тегов" in prompt
    assert "JSON-массив строк" in prompt
    
    # Проверка, что текст присутствует в промпте
    for word in case["text"].split()[:3]:  # Первые 3 слова
        assert word in prompt


def test_prompt_handles_empty_text():
    """Промпт корректно обрабатывает пустой текст."""
    text = ""
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    # Промпт должен содержать правила даже для пустого текста
    assert "подстрокой исходного текста" in prompt
    assert "пустой массив []" in prompt


def test_prompt_handles_special_characters():
    """Промпт корректно обрабатывает специальные символы."""
    text = "Тест с эмодзи 🚀 и символами #hashtag @mention"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    # Проверка, что текст присутствует
    assert "Тест с эмодзи" in prompt
    assert "символами" in prompt


def test_prompt_requires_clean_tags():
    """Промпт требует чистые теги без пунктуации."""
    text = "Тестовый текст"
    prompt = STRICT_TAGGING_PROMPT.format(text=text)
    
    assert "Никаких хэштегов" in prompt
    assert "знаков #" in prompt
    assert "кавычек" in prompt
    assert "эмодзи" in prompt
    assert "пунктуации" in prompt
    assert "чистый» текст тега" in prompt
