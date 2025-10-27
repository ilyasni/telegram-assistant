# Crawl4AI Dockerfile с корректной установкой Playwright
# [C7-ID: DOCKER-CRAWL4AI-002]

FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей для Playwright
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Установка Playwright browsers ПОСЛЕ установки пакета
RUN python -m playwright install --with-deps chromium

# Создание временной директории для Playwright
ENV TMPDIR=/tmp/playwright
RUN mkdir -p /tmp/playwright && chmod 1777 /tmp/playwright

# Копирование исходного кода
COPY . .

# Health check endpoint
EXPOSE 8080

# Запуск сервиса
CMD ["python", "-u", "main.py"]