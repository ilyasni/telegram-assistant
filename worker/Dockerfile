FROM python:3.11-slim

WORKDIR /app

# [C7-ID: dev-mode-006] Оптимизация слоёв: системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# [C7-ID: dev-mode-006] Оптимизация кеша: копируем requirements отдельно
COPY worker/requirements.txt .

# [C7-ID: dev-mode-006] Используем cache mount для pip (Context7 best practice)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# [C7-ID: CODE-CLEANUP-022] Установка shared пакета (build context теперь корень репозитория)
COPY shared/ /app/shared/
RUN pip install -e /app/shared/python

# [C7-ID: dev-mode-006] Копируем код ПОСЛЕ установки зависимостей
COPY worker/ .

# Context7: Копируем скрипты для pipeline health check
RUN mkdir -p /app/scripts /app/config
COPY scripts/check_pipeline_health.py /app/scripts/
COPY scripts/check_pipeline_e2e.py /app/check_pipeline_e2e.py
COPY scripts/reindex_neo4j_relationships.py /app/scripts/
COPY scripts/pipeline_health_cron.sh /app/scripts/
COPY config/slo_thresholds.json /app/config/

# Создание пользователя для безопасности
RUN useradd -m -u 1000 worker && chown -R worker:worker /app
USER worker

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-u", "run_all_tasks.py"]
