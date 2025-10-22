FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Context7 best practice: настройка timezone
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Копирование requirements и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание пользователя для безопасности
RUN useradd -m -u 1000 telethon && chown -R telethon:telethon /app
USER telethon

# Health check (Context7 best practice: используем порт 8011 для health check)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8011/health', timeout=5)" || exit 1

# Context7 best practice: жёстко фиксируем entrypoint с faulthandler
CMD ["python", "-X", "faulthandler", "main.py"]
