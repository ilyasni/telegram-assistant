# Vision Rollback Checklist (Context7)

## 1. Фич-флаги и переменные окружения

| Возможность | Флаг/ENV | Текущее значение (рекоменд.) | Действие при откате |
|-------------|----------|-------------------------------|----------------------|
| Wave A: downscale/grayscale | `VISION_PREPROCESS_ENABLED`, `VISION_PREPROCESS_GRAYSCALE`, `VISION_MAX_OUTPUT_TOKENS` | `true` / `true` / `512` | Установить `VISION_PREPROCESS_ENABLED=false`, перезапустить worker. |
| Wave B: локальный OCR | `VISION_LOCAL_OCR_PRIMARY_ENABLED`, `VISION_PHASH_ENABLED`, `VISION_PHASH_REDIS_PREFIX` | `true` / `true` / `vision:phash` | Поставить `false`, очистить Redis ключи `vision:phash:*` (при необходимости). |
| Wave C: ROI/low priority | `VISION_ROI_CROP_ENABLED`, `VISION_LOW_PRIORITY_QUEUE_ENABLED`, `VISION_LOW_PRIORITY_STREAM` | `true` / `true` / `stream:posts:vision:low` | Сбросить в `false`, очистить поток `XTRIM stream:posts:vision:low 0`. |
| Tokens count | `VISION_TOKENS_COUNT_ENABLED` | `true` | Установить `false`, метрика `vision_tokens_estimated_total` перестанет расти. |
| Experiments | `vision_experiments.yml` (`wave_a/b/c.force_tenants`) | Custom | Выставить `enabled: false` или вернуть `default_variant: control`. |
| Sandbox PoC | `FEATURE_VISION_COMPRESSION_POC` | `false` | Оставить `false` в прод, при необходимости убрать из `.env`. |

## 2. Redis / S3 / Postgres

- **Метки Vision**: `vision:processed:*` хранит идемпотентность. Очистка не требуется при откате, но актуализировать TTL (`VISION_IDEMPOTENCY_TTL_HOURS`) при смене логики.
- **Low priority stream**: `stream:posts:vision:low`. Перед выключением фичи выполнить `XTRIM` или `DEL`, чтобы не было «подвешенных» сообщений.
- **S3 кэш**: результаты Vision лежат в `vision/{tenant}/{sha256}_{model}.json`. Откат не требует чистки, но при смене схемы можно удалить с `s3_service.delete_objects`.
- **Postgres `post_enrichment`**: поля `analysis.context.experiments` и `analysis.context.tokens_estimated` сохраняются; для отчистки можно выполнить `UPDATE ... SET data = data - 'context'`.

## 3. Мониторинг

Перед отключением фич убедиться, что нет активного эксперимента (метрика `vision_experiment_assignments_total`). После — проверить, что:

- `vision_media_total{reason="ocr_primary"}` возвращается к 0, если OCR отключён.
- `vision_tokens_estimated_total` перестаёт увеличиваться при выключении tokens_count.
- `vision_low_priority_*` остаются нулевыми после очистки очереди.

Рекомендуемый порядок действий:

1. Зафиксировать текущие метрики (`export_vision_baseline.py`).
2. Выключить фич-флаг → перезапустить worker.
3. Очистить связанные артефакты (Redis stream/caches).
4. Проверить метрики/логи.

## 4. Документация и команды

- Главные изменения отражены в `docs/VISION_S3_INTEGRATION.md`, `docs/research/VISION_COMPRESSION_POC.md`.
- Для оперативного отката рекомендуется хранить `.env` шаблон с секцией Vision.
- Команды:

```bash
# Перезапуск worker
docker compose restart worker

# Очистка low priority стрима
redis-cli -u $REDIS_URL XTRIM stream:posts:vision:low 0

# Сброс phash-кэша (при необходимости)
redis-cli -u $REDIS_URL KEYS "vision:phash:*" | xargs -r redis-cli -u $REDIS_URL DEL
```

## 5. Контакты/ответственные

- **Vision**: @vision-owner
- **SRE / DevOps**: @devops-oncall
- **Документация Context7**: `docs/VISION_*`

