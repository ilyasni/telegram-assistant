"""Unit-тесты для строгого промпта тегирования."""

import pytest

from worker.prompts.tagging import STRICT_TAGGING_PROMPT


RULES = [
    "Найди 3-5 ключевых слов",
    "Используй слова и фразы, которые есть в тексте",
    "Можно использовать отдельные слова или короткие фразы",
    "Избегай общих категорий",
    "Формат ответа: только JSON-массив строк",
    "Пример: [\"Python\", \"релиз\", \"производительность\"]",
    "Если подходящих тегов нет — верни пустой массив []",
]


def format_prompt(text: str) -> str:
    return STRICT_TAGGING_PROMPT.format(text=text)


def test_prompt_injects_text():
    text = "Газпром нефть открыл хаб в Омске для авиакеросина."
    prompt = format_prompt(text)
    assert text in prompt


def test_prompt_contains_core_rules():
    prompt = format_prompt("пример")
    for rule in RULES:
        assert rule in prompt


@pytest.mark.parametrize(
    "sample",
    [
        "ЦБ опубликовал обзор ликвидности банковского сектора.",
        "Сегодня стрим в 19:00, разбор бэктестов.",
        "Тест с эмодзи 🚀 и символами #hashtag @mention",
    ],
)
def test_prompt_handles_various_inputs(sample):
    prompt = format_prompt(sample)
    assert sample in prompt


def test_prompt_requires_json_array():
    prompt = format_prompt("Проверка формата")
    assert "JSON-массив строк" in prompt
    assert "без markdown" in prompt


def test_prompt_example_present():
    prompt = format_prompt("example")
    assert "Пример: [\"Python\", \"релиз\", \"производительность\"]" in prompt
