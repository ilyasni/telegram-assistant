# Crawl4AI Dockerfile с корректной установкой Playwright
# [C7-ID: DOCKER-CRAWL4AI-002]

FROM python:3.11-slim

WORKDIR /app

# [C7-ID: dev-mode-006] Оптимизация слоёв: системные зависимости для Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# [C7-ID: dev-mode-006] Оптимизация кеша: копируем requirements отдельно
COPY requirements.txt .

# [C7-ID: dev-mode-006] Используем cache mount для pip (Context7 best practice)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Установка Playwright browsers ПОСЛЕ установки пакета
RUN python -m playwright install --with-deps chromium

# Создание временной директории для Playwright
ENV TMPDIR=/tmp/playwright
RUN mkdir -p /tmp/playwright && chmod 1777 /tmp/playwright

# [C7-ID: dev-mode-006] Копируем код ПОСЛЕ установки зависимостей
COPY . .

# Health check endpoint
EXPOSE 8080

# Запуск сервиса
CMD ["python", "-u", "main.py"]