# Phase 0 — локальный PaddleOCR (CPU)

Цель фазы: развернуть локальный OCR без GPU и получить метрики латентности / пропускной способности перед включением его в decision-graph Vision.

## 1. Подготовка окружения

PaddleOCR вынесен в отдельный сервис. Все зависимости ставим в контейнере
или локальном виртуальном окружении по файлу `services/paddleocr/requirements.txt`.

Переменные окружения (опционально):

| ENV | Назначение | Значение по умолчанию |
| --- | --- | --- |
| `LOCAL_OCR_LANG_PRIORITY` | приоритет языков в ответе | `ru,en` |
| `LOCAL_OCR_PRELOAD` | загрузить модели при старте сервиса | `false` |
| `LOCAL_OCR_DET_SIDE_LEN` | максимальная сторона (пиксели) | `960` |

## 2. Запуск сервиса командой

Локальный запуск (без Docker):

```bash
python -m venv .venv-paddleocr
source .venv-paddleocr/bin/activate
pip install -r services/paddleocr/requirements.txt
uvicorn services.paddleocr.app.server:app --host 0.0.0.0 --port 8008 --timeout-keep-alive 120
```

Endpoints:

- `GET /healthz` — проверка готовности
- `POST /v1/ocr` — JSON `{image_base64, languages?, return_image?}`
- `POST /v1/ocr/upload` — `multipart/form-data` с файлом
- `GET /metrics` — Prometheus метрики (`local_ocr_requests_total`, `local_ocr_request_duration_seconds`, `local_ocr_processing_active`)

## 3. Docker Compose (override)

В репозитории есть готовый `docker-compose.paddleocr.yml`, который собирает образ
на базе `paddlepaddle/paddle:2.6.1` (CPU) и устанавливает все зависимости заранее.

```bash
# Построить образ (один раз)
docker compose -f docker-compose.yml -f docker-compose.paddleocr.yml build paddleocr

# Поднять сервис под именем paddleocr
docker compose -f docker-compose.yml -f docker-compose.paddleocr.yml up paddleocr
```

> Базовый образ Paddle уже содержит `libgomp1`. Если запускаешь вручную в другом образе,
> убедись, что пакет установлен (`apt-get install -y libgomp1`).

## 4. Бенчмарк на своем железе

Собрать выборку изображений (`.png/.jpg/.jpeg/.bmp/.webp/.tiff`) и запустить скрипт:

```bash
python services/paddleocr/scripts/benchmark_local_ocr.py \
  --images-dir ./artifacts/vision-samples \
  --mode local \
  --max-files 200 \
  --json > reports/ocr_local_phase0.json
```

Для проверки сервиса через HTTP:

```bash
python services/paddleocr/scripts/benchmark_local_ocr.py \
  --images-dir ./artifacts/vision-samples \
  --mode http \
  --endpoint http://localhost:8008/v1/ocr
```

В отчёте фиксируется:

- среднее / медиана / p95 латентности (`ms`)
- min/max
- пропускная способность (изображений в секунду)
- среднее число строк и confidence

## 5. Интерпретация результатов

- **Целевая латентность**: ≤ 350–400 ms для документов, ≤ 200 ms для скриншотов/простых фото.
- **Пропускная способность**: ≥ 2 img/s на выделенном CPU core.
- Если показатели выше — планируем включение локального OCR как «фильтр» вместо Vision для doc/screenshot.
- При отклонении: анализировать CPU профили, уменьшать `det_limit_side_len`, тестировать PaddleOCR для конкретного языка.

## 6. Следующие шаги после Phase 0

1. Сравнить извлечённый текст с GigaChat Vision OCR (выборка 50–100 изображений).
2. Зафиксировать цифры в отчёте и принять решение о включении фильтра в decision-graph.
3. Подготовить Feature Flag для подключения локального OCR в `VisionPolicyEngine`.

