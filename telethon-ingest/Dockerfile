FROM python:3.11-slim

WORKDIR /app

# [C7-ID: dev-mode-006] Оптимизация слоёв: системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Context7 best practice: настройка timezone
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# [C7-ID: dev-mode-006] Оптимизация кеша: копируем requirements отдельно
COPY requirements.txt .

# [C7-ID: dev-mode-006] Используем cache mount для pip (Context7 best practice)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# [C7-ID: dev-mode-006] Копируем код ПОСЛЕ установки зависимостей
COPY . .

# Создание пользователя для безопасности
RUN useradd -m -u 1000 telethon && chown -R telethon:telethon /app
USER telethon

# Health check (Context7 best practice: используем порт 8011 для health check)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8011/health', timeout=5)" || exit 1

# Context7 best practice: жёстко фиксируем entrypoint с faulthandler
CMD ["python", "-X", "faulthandler", "main.py"]
