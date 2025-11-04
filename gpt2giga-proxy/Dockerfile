FROM python:3.11-slim

# Системные зависимости
RUN apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Отключение проверки SSL на уровне окружения (для работы без сертификатов Минцифры)
# Context7: Используем переменные окружения для отключения SSL проверки
ENV PYTHONHTTPSVERIFY=0
ENV CURL_CA_BUNDLE=""
ENV REQUESTS_CA_BUNDLE=""
ENV SSL_CERT_FILE=""
# Дополнительно отключаем проверку SSL для httpx (используется в gigachat)
ENV HTTPX_VERIFY=false

# Установка официального пакета gpt2giga
RUN pip install --no-cache-dir gpt2giga

WORKDIR /app

# Копируем патч для отключения SSL и Python wrapper
COPY disable_ssl.py /app/disable_ssl.py
COPY run_gpt2giga.py /app/run_gpt2giga.py
RUN chmod +x /app/run_gpt2giga.py

# Health check согласно документации
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PROXY_PORT:-8090}/v1/models || exit 1

# Запуск согласно документации gpt2giga
# Используем переменные окружения (GIGACHAT_VERIFY_SSL_CERTS по умолчанию False)
# Загружаем SSL патч перед запуском gpt2giga
CMD python3 -c "import sys; sys.path.insert(0, '/app'); import disable_ssl" && exec gpt2giga \
    --proxy-host ${GPT2GIGA_HOST:-0.0.0.0} \
    --proxy-port ${GPT2GIGA_PROXY_PORT:-8090} \
    --proxy-log-level ${GPT2GIGA_LOG_LEVEL:-INFO} \
    --proxy-pass-model \
    --proxy-timeout ${GPT2GIGA_TIMEOUT:-600} \
    --proxy-embeddings ${GPT2GIGA_EMBEDDINGS:-EmbeddingsGigaR}