FROM python:3.11-slim

# Системные зависимости
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Установка официального пакета gpt2giga
RUN pip install --no-cache-dir gpt2giga

WORKDIR /app

# Health check согласно документации
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PROXY_PORT:-8090}/v1/models || exit 1

# Запуск с рекомендуемыми флагами из документации
CMD ["gpt2giga", \
     "--host", "0.0.0.0", \
     "--port", "8090", \
     "--verbose", \
     "--pass-model", \
     "--timeout", "600"]