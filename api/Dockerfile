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
RUN useradd -m -u 1000 api && chown -R api:api /app
USER api

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
