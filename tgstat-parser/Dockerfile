# TGStat Parser Service Dockerfile
# Context7: Минимальный образ для безопасности и производительности

FROM python:3.11-slim

WORKDIR /app

# Context7: Установка системных зависимостей для Selenium
# Context7: Chrome, Xvfb и зависимости для non-headless режима (обход Cloudflare)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    wget \
    gnupg2 \
    ca-certificates \
    fonts-liberation \
    xvfb \
    x11-utils \
    x11-xserver-utils \
    xauth \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    libu2f-udev \
    libvulkan1 \
    && rm -rf /var/lib/apt/lists/*

# Context7: Установка Google Chrome (скачивание .deb напрямую)
# Context7: Сначала устанавливаем зависимости, затем Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    libu2f-udev \
    libvulkan1 \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome.deb

# Context7: Настройка timezone
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Context7: Копирование requirements для оптимизации кеша слоёв
COPY tgstat-parser/requirements.txt .

# Context7: Установка зависимостей с cache mount
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Context7: Копирование кода после установки зависимостей
COPY tgstat-parser/ .

# Context7: Создание непривилегированного пользователя для безопасности
RUN useradd -m -u 1000 tgstat && chown -R tgstat:tgstat /app
USER tgstat

# Context7: Health check на порту 8020
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health', timeout=5)" || exit 1

# Context7: Запуск через xvfb-run для non-headless режима (обход Cloudflare)
# Context7: --auto-servernum автоматически выбирает свободный display номер
# Context7: --server-args задаёт разрешение виртуального экрана
# Context7: DISPLAY переменная устанавливается автоматически xvfb-run
# Context7: Используем shell form для правильной обработки аргументов xvfb-run
CMD xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24 -ac +extension GLX +render -noreset" python -X faulthandler main.py
