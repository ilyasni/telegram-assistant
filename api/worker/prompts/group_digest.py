"""
Промпты для мультиагентного пайплайна групповых дайджестов.

Context7:
- Все промпты в одном месте с версионированием.
- Каждый промпт содержит строгие guardrails и JSON контракты.
"""

from __future__ import annotations

from textwrap import dedent

from langchain_core.prompts import ChatPromptTemplate


def thread_builder_prompt_v1() -> ChatPromptTemplate:
    """Промпт для выделения дискуссионных веток."""
    system = dedent(
        """\
        Ты анализируешь сообщения Telegram-группы и формируешь дискуссионные ветки.
        Правила:
        - Работай только на русском языке.
        - Учитывай reply_to, временную близость и похожесть текста.
        - Не превышай 20 сообщений на ветку; если больше — разбей на части и добавь суффикс '-partN'.
        - Возвращай только JSON по схеме ниже. Никаких комментариев или Markdown.
        - Если данных недостаточно, верни пустой массив threads.
        - Поле similarity_reason опиши словами, почему сообщения объединены (<=120 символов).
        - При сомнении используй пустой список msg_ids.
        """
    )
    human = dedent(
        """\
        Входные данные:
        - messages_json: список сообщений с полями message_id, timestamp_iso, username, content, reply_to_id.
        - max_thread_len: максимальная длина ветки.

        Верни JSON строго по схеме:
        {{
          "threads": [
            {{
              "thread_id": "thread-001",
              "msg_ids": ["123", "124"],
              "start_ts": "2025-11-10T08:15:01+00:00",
              "end_ts": "2025-11-10T08:24:59+00:00",
              "reply_root": "122",
              "similarity_reason": "Обсуждение бага вебвью"
            }}
          ]
        }}

        messages_json:
        {messages_json}
        max_thread_len: {max_thread_len}
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def semantic_segmenter_prompt_v1() -> ChatPromptTemplate:
    """Промпт для семантического сегментирования веток."""
    system = dedent(
        """\
        Ты выделяешь семантические блоки внутри ветки обсуждения.
        Допустимые типы блоков: problem, solution, decision, risk, celebration, meta, humor.
        Ответ только JSON по схеме. Указывай confidence от 0 до 1.
        Игнорируй стикеры, эмодзи, пустые сообщения. Если блок неопределён — kind='meta'.
        
        КРИТИЧЕСКИ ВАЖНО:
        - Если сообщений ≥ 30, обязательно выдели не менее 2-3 тем/блоков
        - Запрещается писать, что темы не выявлены. Если сложно — опирайся на ключевые слова и связки ответов
        - Если всё кажется одной темой — разбей по подподходам/под-вопросам
        - Используй reply_to связи для группировки связанных сообщений
        """
    )
    human = dedent(
        """\
        Данные ветки:
        thread_id: {thread_id}
        messages:
        {thread_messages}

        Верни JSON вида:
        {{
          "thread_id": "t1",
          "units": [
            {{
              "kind": "problem",
              "text": "...",
              "msg_ids": ["id1", "id2"],
              "offset_range": [0, 120],
              "confidence": 0.84
            }}
          ]
        }}

        Если нет смысловых блоков — верни пустой массив units.
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def semantic_segmenter_repair_prompt_v1() -> ChatPromptTemplate:
    """Промпт для восстановления JSON сегментера."""
    system = (
        "Ты исправляешь JSON-ответ агента семантического сегментирования.\n"
        "Нужно устранить ошибки, указанные в списке, и вернуть корректный JSON."
    )
    human = (
        "Контекст ветки:\n"
        "{thread_messages}\n\n"
        "Предыдущий ответ:\n"
        "{invalid_json}\n\n"
        "Ошибки валидации:\n"
        "{errors}\n\n"
        "Верни исправленный JSON по схеме segmenter_agent.\n"
        "Не добавляй текст вне JSON."
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def emotion_analyzer_prompt_v1() -> ChatPromptTemplate:
    """Промпт для анализа эмоций."""
    system = dedent(
        """\
        Ты эмоциональный аналитик. Оцени общий тон разговора, конфликтность и коллаборацию.
        Верни JSON по схеме. Значения intensity, conflict, collaboration, stress, enthusiasm в диапазоне [0,1].
        Поле tone ∈ {{positive, neutral, negative}}. Если нет данных — tone='neutral'.
        
        Требования:
        - Сфокусируйся на метриках, без излишних описаний
        - notes — кратко (1-2 предложения), без HR-поэтики
        - Используй данные из сообщений, не выдумывай
        """
    )
    human = dedent(
        """\
        Сводка окна:
        window: {window_json}
        messages:
        {messages_sample}

        JSON шаблон:
        {{
          "tone": "neutral",
          "intensity": 0.45,
          "conflict": 0.22,
          "collaboration": 0.64,
          "stress": 0.31,
          "enthusiasm": 0.55,
          "notes": "Коротко опиши драйверы эмоций."
        }}
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def emotion_analyzer_repair_prompt_v1() -> ChatPromptTemplate:
    """Промпт для восстановления JSON эмоций."""
    system = "Исправь JSON эмоционального профиля, соблюдай диапазоны значений и обязательные поля."
    human = (
        "Сводка окна:\n{window_json}\n"
        "Фрагменты сообщений:\n{messages_sample}\n\n"
        "Неверный ответ:\n{invalid_json}\n\n"
        "Ошибки валидации:\n{errors}\n\n"
        "Верни корректный JSON."
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def role_classifier_prompt_v1() -> ChatPromptTemplate:
    """Промпт для классификации ролей участников."""
    system = dedent(
        """\
        Ты определяешь социальные роли участников обсуждения.
        Допустимые роли: initiator, expert, moderator, supporter, opponent, observer.
        Требования:
        - Для каждого участника указывай массив roles с весами (0..1, сумма ≤ 1).
        - dominant_role = роль с максимальным весом (если нет, observer).
        - comment — лаконичное (≤120 символов) пояснение вклада и поведения с отсылкой к типу сообщений.
        - Всегда возвращай минимум 5 участников или всех, если их меньше.
        - message_ids — обязательное поле с ID сообщений, на основе которых присвоена роль.
        
        КРИТИЧЕСКИ ВАЖНО:
        - Запрещается выдумывать роли без ссылок на конкретные сообщения
        - Для каждого участника, которому присваиваешь роль, дай 1-2 фразы резюме с отсылкой к типу сообщений (инициировал темы, задавал вопросы, давал решения и т.п.)
        - Если модель не уверена в роли — не присваивай её (не включай участника в результат)
        - Роли должны отражать функции: "инициатор темы", "много отвечал", "задавал уточняющие вопросы", а не абстрактные "supporter/opponent"
        
        Ответ строго JSON по схеме.
        """
    )
    human = dedent(
        """\
        Статистика участия:
        {participant_stats}
        Семантические сегменты:
        {semantic_units}

        Формат ответа:
        {{
          "participants": [
            {{
              "username": "alice",
              "roles": [{{"name": "initiator", "weight": 0.7}}, {{"name": "expert", "weight": 0.3}}],
              "dominant_role": "initiator",
              "message_ids": ["id1", "id5"],
              "comment": "Запустила обсуждение и дала экспертные рекомендации"
            }}
          ]
        }}
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def role_classifier_repair_prompt_v1() -> ChatPromptTemplate:
    """Промпт для восстановления JSON классификатора ролей."""
    system = (
        "Исправь JSON с ролями участников. Следи за валидностью массива participants и role_profile."
    )
    human = (
        "Статистика участия:\n{participant_stats}\n"
        "Сегменты обсуждения:\n{semantic_units}\n\n"
        "Неверный ответ:\n{invalid_json}\n\n"
        "Ошибки валидации:\n{errors}\n\n"
        "Верни корректный JSON."
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def topic_synthesizer_prompt_v1() -> ChatPromptTemplate:
    """Промпт для агрегирования тем."""
    system = dedent(
        """\
        Ты агрегируешь ключевые темы окна обсуждения.
        
        КРИТИЧЕСКИ ВАЖНО:
        - Всегда верни минимум 1 тему, даже если обсуждение хаотичное. Если тем несколько, верни 2-5 тем.
        - Запрещается возвращать пустой массив topics.
        
        Требования:
        - Максимум 5 тем, отсортированных по приоритету (critical > high > medium > low) и msg_count.
        - Для каждой темы обязательно укажи:
          title — должен содержать ключевые слова из сообщений (не общие фразы типа "Общее обсуждение")
          priority, msg_count, threads — обязательны
          message_ids — массив ID ключевых сообщений по теме (обязательно, если есть)
          summary — 2–3 предложения с сутью и аргументами.
          decision — финальный вывод, даже если «Решение не принято».
          status — одно из: done, in_progress, blocker, watch, idea, ongoing, unclear.
          owners — массив usernames ключевых владельцев.
          blockers — массив препятствий или пустой список.
          actions — список строк «@owner — действие — срок/статус». Если найдено хоть одно "давай сделаем/надо/предлагаю" — обязательно включи в actions.
          signals — словарь метрик (tone, risk, impact, progress).
          keywords — до 5 ключевых слов из сообщений.
          Если в теме есть вложения, включи их краткое описание из media_highlights_json в summary или actions.
        
        ИЗВЛЕЧЕНИЕ РЕШЕНИЙ И ДОГОВОРЁННОСТЕЙ:
        - Считай решениями/договорённостями фразы, где есть:
          решили, договорились, давай сделаем, давайте так, ок, по рукам, закрепляем, будем делать, надо сделать, я беру, я сделаю, беру на себя, могу сделать, предлагаю.
        - Если решение только обсуждалось, но не зафиксировано явно, напиши:
          "decision": "Обсуждали X, но финального решения нет."
        - Если в сообщениях нет явного решения, не придумывай его. Заполни:
          "decision": "Явного решения не найдено"
          и не заполняй "actions" (оставь пустым массивом).
        
        ИЗВЛЕЧЕНИЕ ВЛАДЕЛЬЦЕВ:
        - Владельцы темы — это участники, которые:
          * Инициировали обсуждение темы
          * Предложили решение или взяли на себя действие
          * Упомянуты в сообщениях с решениями (например, "Аня, сделай пожалуйста")
          * Написаны через @упоминание в контексте темы
        
        Action items должны перекрывать ключевые договорённости.
        Запрещается возвращать темы с title ∈ {"Общее обсуждение", "Разное", "Без темы"} если есть достаточно сообщений для выделения конкретных тем.
        Если темы выделить нельзя, верни одну тему «Общее обсуждение» со статусом watch только как последний вариант.
        Ответ строго JSON.
        """
    )
    human = dedent(
        """\
        Сегменты обсуждения:
        {semantic_units}
        Эмоции:
        {emotion_profile}
        Медиа вложения:
        {media_highlights_json}

        Примеры формата ответа:
        
        Пример 1 (есть решение):
        {{
          "topics": [
            {{
              "title": "Проблема с вебвью",
              "priority": "high",
              "msg_count": 15,
              "threads": ["thread-1-part1"],
              "message_ids": ["msg-123", "msg-124"],
              "summary": "Обнаружена проблема с нагрузкой вебвью. Пользователи жалуются на медленную загрузку.",
              "decision": "Обновить клиент до версии 2.3 до пятницы. Решили, что @boyversus протестирует фикс.",
              "status": "in_progress",
              "owners": ["boyversus"],
              "blockers": ["Нужна тестовая среда"],
              "actions": ["@boyversus — протестировать фикс на стенде — 15.11"],
              "signals": {{"tone": "neutral", "risk": "medium", "impact": "high", "progress": "0.6"}},
              "keywords": ["вебвью", "нагрузка"]
            }}
          ]
        }}
        
        Пример 2 (решения нет, только обсуждение):
        {{
          "topics": [
            {{
              "title": "Дизайн новой страницы",
              "priority": "medium",
              "msg_count": 8,
              "threads": ["thread-2"],
              "message_ids": ["msg-200", "msg-201"],
              "summary": "Обсуждали варианты дизайна главной страницы. Предложено несколько концепций.",
              "decision": "Обсуждали варианты дизайна, но финального решения нет.",
              "status": "watch",
              "owners": ["designer_alice"],
              "blockers": [],
              "actions": [],
              "signals": {{"tone": "positive", "risk": "low", "impact": "medium", "progress": "0.3"}},
              "keywords": ["дизайн", "страница"]
            }}
          ]
        }}
        
        Пример 3 (организационные вопросы):
        {{
          "topics": [
            {{
              "title": "Распределение задач на спринт",
              "priority": "high",
              "msg_count": 12,
              "threads": ["thread-3"],
              "message_ids": ["msg-300", "msg-301", "msg-302"],
              "summary": "Обсуждали распределение задач между участниками команды на следующий спринт.",
              "decision": "Договорились, что Аня возьмёт задачи по дайджесту, а Боб — по API.",
              "status": "agreed",
              "owners": ["Аня", "Боб"],
              "blockers": [],
              "actions": ["@Аня — задачи по дайджесту", "@Боб — задачи по API"],
              "signals": {{"tone": "neutral", "risk": "low", "impact": "high", "progress": "1.0"}},
              "keywords": ["задачи", "спринт", "распределение"]
            }}
          ]
        }}

        Шаблон ответа (строго JSON, минимум 1 тема):
        {{
          "topics": [
            {{
              "title": "...",
              "priority": "...",
              "msg_count": ...,
              "threads": [...],
              "message_ids": [...],
              "summary": "...",
              "decision": "...",
              "status": "...",
              "owners": [...],
              "blockers": [...],
              "actions": [...],
              "signals": {{...}},
              "keywords": [...]
            }}
          ]
        }}
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def topic_synthesizer_repair_prompt_v1() -> ChatPromptTemplate:
    """Промпт для восстановления JSON тем."""
    system = (
        "Исправь JSON с темами. Убедись, что каждая тема содержит summary, decision, status, actions, owners и не превышен лимит 5 тем. Если media_highlights_json содержит вложения, отрази ключевые вложения в summary или actions."
    )
    human = (
        "Сегменты обсуждения:\n{semantic_units}\n"
        "Эмоции:\n{emotion_profile}\n\n"
        "Неверный ответ:\n{invalid_json}\n\n"
        "Ошибки валидации:\n{errors}\n\n"
        "Верни корректный JSON."
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def digest_composer_prompt_v1() -> ChatPromptTemplate:
    """Промпт для генерации Telegram HTML дайджеста."""
    system = dedent(
        """\
        Ты генерируешь Telegram HTML дайджест обсуждения.
        Требования:
        - Используй только теги: <b>, <i>, <u>, <code>, <pre>, <a>, <ul>, <ol>, <li>, <br>.
        - Общая длина (после заголовка) ≤ 1200 символов, без воды.
        - Структура:
          1. Заголовок: `📊 <b>Дайджест: {group_title}</b> | {period} | {message_count} сообщений`.
          2. Блок `🎯 <b>Основные темы</b>` c <ul>, основанный на topics_json (отсортируй по priority и msg_count). Каждый <li> содержит:
             • название и приоритет;
             • краткое summary (2 предложения);
             • <b>Решение:</b> (поле decision);
             • статус с эмодзи (done → ✅, in_progress → 🚧, blocker → ⚠, watch → 👀, idea → 💡);
             • ключевых владельцев/owners;
             • список действий (actions) через <br>.
          3. Блок `👥 <b>Активные участники</b>` с top-10 участников из participants_json (сортировка по message_count). Формат `<li><b>@username</b> — роль, сообщений: N. Комментарий: ...</li>`. Если username отсутствует, используй placeholder `без никнейма`. Если участников меньше 10, перечисли всех, не используй формулировку «Не указаны».
          4. Блок `🖼 <b>Заметные вложения</b>` — до 5 пунктов из media_highlights_json. Формат `<li><b>Тип</b> — краткое описание (участник, если есть). {опционально: ярлык}</li>`. Если вложений нет, всё равно добавь строку «Вложения отсутствуют».
          5. Блок `📌 <b>Итоги</b>` — выжимка решений, эмоциональный тон (tone, intensity, conflict/collaboration/stress/enthusiasm из metrics_json) и сводка по медиа (media_stats_json: количество вложений, типов, доля без описаний).
          6. Блок `🚀 <b>Следующие действия</b>` — агрегируй уникальные действия (actions). Формат `<li>@owner — действие — срок/статус</li>`. Если действий нет, напиши об этом явно.
        - Не цитируй сообщения целиком, не используй Markdown.
        - Используй role_profile_json для уточнения ролей при необходимости.
        """
    )
    human = dedent(
        """\
        window:
        {window_json}
        topics:
        {topics_json}
        participants:
        {participants_json}
        role_profile:
        {role_profile_json}
        metrics:
        {metrics_json}
        media_highlights:
        {media_highlights_json}
        media_stats:
        {media_stats_json}
        baseline_digest:
        {baseline_digest}

        Сформируй итоговый HTML дайджест по указанной структуре.
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def digest_composer_retry_prompt_v1() -> ChatPromptTemplate:
    """Промпт для корректирующего синтеза дайджеста (compact mode)."""
    system = dedent(
        """\
        Сформируй улучшенную версию дайджеста, соблюдая требования базового промпта.
        Особое внимание:
        - Точность решений и статусов.
        - Удаление повторов и лишних слов.
        - Чёткие формулировки для actions.
        Используй только whitelist-теги и удерживай длину ≤ 1200 символов.
        В блоках участников, вложений и итогов следуй тем же правилам, что и в базовом промпте (top-10, эмо-маркеры, статистика по медиа).
        """
    )
    human = dedent(
        """\
        window:
        {window_json}
        topics:
        {topics_json}
        participants:
        {participants_json}
        role_profile:
        {role_profile_json}
        metrics:
        {metrics_json}
        media_highlights:
        {media_highlights_json}
        media_stats:
        {media_stats_json}
        baseline_digest:
        {baseline_digest}

        Сделай дайджест более структурированным, подчеркни ключевые решения и изменения относительно baseline.
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def digest_composer_prompt_v2() -> ChatPromptTemplate:
    """Промпт v2 для генерации Telegram HTML дайджеста с жёсткой структурой и конкретикой."""
    system = dedent(
        """\
        Ты генерируешь Telegram HTML дайджест обсуждения с фокусом на конкретику и решения.
        Требования:
        - Используй только теги: <b>, <i>, <u>, <code>, <pre>, <a>, <ul>, <ol>, <li>, <br>.
        - Общая длина (после заголовка) ≤ 2000 символов, без воды и HR-поэтики.
        - ЖЁСТКАЯ СТРУКТУРА (соблюдай порядок):
        
        1. Заголовок: `📊 <b>Дайджест: {group_title}</b> | {period} | {message_count} сообщений`
        
        2. <b>🎯 Что обсуждали</b>:
           - Если topics_json пустой, но есть messages в window_json — сам сформируй 1-3 темы на основе сообщений и покажи их в тексте дайджеста как "Тема 1: ...", "Тема 2: ..."
           - Если тем только 1 — всё равно делай структурированный дайджест с подзаголовками, не требуй 2+ тем
           - Для каждой темы из topics_json (минимум 1, максимум 5) используй формат:
             <ul>
             <li><b>Название темы</b> (должно содержать ключевые слова из сообщений)<br>
             1-2 предложения с сутью обсуждения<br>
             <b>Решение:</b> конкретная формулировка из поля decision (если нет — "Конкретных договорённостей не зафиксировано")<br>
             Если есть message_links в теме — добавь ссылку: "Ключевые сообщения: [используй готовые ссылки из поля message_links, разделённые запятыми]"<br>
             Статус с эмодзи: done → ✅, in_progress → 🚧, blocker → ⚠, watch → 👀, idea → 💡, agreed → ✅, ongoing → 🚧, unclear → ❓</li>
             </ul>
           - ВАЖНО: Используй готовые ссылки из поля message_links темы. НЕ формируй ссылки самостоятельно.
           - Запрещается писать "Основные темы: Не выявлены" или "из-за отсутствия данных"
           - ЗАПРЕЩАЕТСЯ использовать символы ▸, •, - для списков. ВСЕГДА используй <ul><li>
        
        3. <b>✅ Решения и договорённости</b>:
           - Используй формат списка:
             <ul>
             <li>Кто что берёт (owners из topics)</li>
             <li>До когда (если указано в actions)</li>
             <li>Какие шаги (actions из topics)</li>
             </ul>
           - Если решений нет — напиши явно: "Конкретных договорённостей не зафиксировано"
           - ЗАПРЕЩАЕТСЯ использовать символы ▸, •, - для списков. ВСЕГДА используй <ul><li>
        
        4. <b>❓ Открытые вопросы</b>:
           - Используй формат списка:
             <ul>
             <li>Вопрос 1 из topics (status: watch, idea, blocker, unclear, ongoing)</li>
             <li>Вопрос 2...</li>
             </ul>
           - Если все закрыты — напиши: "Открытых вопросов нет"
           - ЗАПРЕЩАЕТСЯ использовать символы ▸, •, - для списков. ВСЕГДА используй <ul><li>
        
        5. <b>👥 Активные участники</b> (только top-3 по message_count):
           - ОБЯЗАТЕЛЬНО используй формат:
             <ul>
             <li><b>@username</b> — [роль по функции: инициатор темы/много отвечал/задавал вопросы], сообщений: N. [краткое описание вклада с конкретными примерами]</li>
             <li><b>@username2</b> — ...</li>
             </ul>
           - Для каждого участника приведи конкретный вклад: что он предложил или сделал, хотя бы одну короткую цитату/переказ сообщения
           - Если username отсутствует — используй "без никнейма"
           - Роли по функциям, а не абстрактные (supporter/opponent)
           - Если участников меньше 3 — перечисли всех
           - Пример хорошего описания: "Аня предложила доработать критерии тем дайджеста и взяла на себя сбор примеров"
           - Пример плохого описания: "Аня — активно участвовала в обсуждении"
           - ЗАПРЕЩАЕТСЯ использовать символы ▸, •, - для списков. ВСЕГДА используй <ul><li>
        
        6. <b>📈 По сравнению с прошлым окном</b> (если есть baseline_delta и has_baseline=true):
           - Новые темы: [novel_topics из baseline_delta]
           - Пересечение тем: [topic_overlap из baseline_delta, в процентах]
           - Изменение охвата: [coverage_change из baseline_delta, в процентах, ↑/↓]
           - Изменение качества: [quality_delta из baseline_delta, если есть]
           - Если baseline_delta.has_baseline=false или baseline_delta пуст — пропусти этот блок
        
        7. <b>🖼 Заметные вложения</b> (до 5 из media_highlights_json):
           - Используй формат списка:
             <ul>
             <li><b>Тип</b> — краткое описание (участник, если есть)</li>
             </ul>
           - Если вложений нет — "Вложения отсутствуют"
           - ЗАПРЕЩАЕТСЯ использовать символы ▸, •, - для списков. ВСЕГДА используй <ul><li>
        
        8. <b>💭 Эмоции и атмосфера</b> (1-2 строки, компактно):
           - Краткий вывод из metrics_json: tone, intensity (без детализации)
           - Без HR-поэтики типа "спокойная и конструктивная атмосфера"
        
        КРИТИЧЕСКИ ВАЖНО:
        - Минимум 1 тема (не требуй 2+ тем, если их нет)
        - Все темы должны содержать конкретику, не общие фразы
        - Ссылки на message_ids обязательны, если они есть в topics
        - Не используй формулировки "Не выявлены", "из-за отсутствия данных"
        - Фокус на содержании и решениях, а не на "атмосфере"
        - Если topics_json пустой — используй window_json и messages для генерации тем на лету
        """
    )
    human = dedent(
        """\
        window:
        {window_json}
        topics:
        {topics_json}
        participants:
        {participants_json}
        role_profile:
        {role_profile_json}
        metrics:
        {metrics_json}
        media_highlights:
        {media_highlights_json}
        media_stats:
        {media_stats_json}
        baseline_delta:
        {baseline_delta}
        baseline_digest:
        {baseline_digest}

        Сформируй итоговый HTML дайджест по указанной жёсткой структуре с фокусом на конкретику.
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def quality_evaluator_prompt_v1() -> ChatPromptTemplate:
    """Промпт для оценки качества дайджеста."""
    system = dedent(
        """\
        Ты оцениваешь качество дайджеста по критериям faithfulness, coherence, coverage, focus.
        Все значения в диапазоне [0,1].
        Добавь поле quality_score (среднее из метрик) и notes с рекомендациями. Ответ строго JSON.
        
        КРИТИЧЕСКИЕ ПРОВЕРКИ:
        - Если в дайджесте написано "Основные темы: Не выявлены" при message_count >= 20, это критическая ошибка (coverage=0.0, faithfulness=0.0)
        - Проверь наличие конкретных решений, открытых вопросов, ссылок на сообщения
        - Проверь, что темы содержат конкретику, а не общие фразы
        - Если дайджест содержит формулировки "из-за отсутствия данных" или "не были зафиксированы из-за отсутствия подробных данных" — это признак низкого качества (coverage снижается)
        - Убедись, что дайджест фокусируется на содержании и решениях, а не только на "атмосфере"
        """
    )
    human = dedent(
        """\
        Исходные сообщения:
        {conversation_excerpt}
        Сгенерированный дайджест (HTML):
        {digest_html}

        Шаблон JSON:
        {{
          "faithfulness": 0.82,
          "coherence": 0.76,
          "coverage": 0.71,
          "focus": 0.74,
          "quality_score": 0.76,
          "notes": "Коротко опиши риски или то, что улучшить."
        }}
        """
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def quality_evaluator_repair_prompt_v1() -> ChatPromptTemplate:
    """Промпт для восстановления JSON оценки качества."""
    system = "Исправь JSON метрик качества. Все поля должны быть в диапазоне [0,1]."
    human = (
        "Исходные сообщения:\n{conversation_excerpt}\n"
        "Дайджест:\n{digest_html}\n\n"
        "Неверный ответ:\n{invalid_json}\n\n"
        "Ошибки валидации:\n{errors}\n\n"
        "Верни корректный JSON."
    )
    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


__all__ = [
    "thread_builder_prompt_v1",
    "semantic_segmenter_prompt_v1",
    "semantic_segmenter_repair_prompt_v1",
    "emotion_analyzer_prompt_v1",
    "emotion_analyzer_repair_prompt_v1",
    "role_classifier_prompt_v1",
    "role_classifier_repair_prompt_v1",
    "topic_synthesizer_prompt_v1",
    "topic_synthesizer_repair_prompt_v1",
    "digest_composer_prompt_v1",
    "digest_composer_prompt_v2",
    "digest_composer_retry_prompt_v1",
    "quality_evaluator_prompt_v1",
    "quality_evaluator_repair_prompt_v1",
]

