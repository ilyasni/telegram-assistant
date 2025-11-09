# Vision Compression PoC (Context7)

## Цель

Снизить объём визуальных токенов GigaChat Vision за счёт офлайн-применения методов V²Drop, AdaViT, ToMe перед вызовом модели, не жертвуя качеством OCR/классификации.

## Исходные данные

- **Датасет**: 200 документов/скриншотов (база Phase 0) + 50 фото/инфографик для проверки универсальности.
- **Разметка**: референс — результат текущего Vision (до оптимизаций) + label `target_tokens_total` из `response.usage.total_tokens`.
- **Метрики** (Context7 приоритет):
  - `tokens_reduction_pct` (до/после вызова) — обязательный KPI.
  - OCR WER (по PaddleOCR результату) для документов/скринов.
  - CLIP-косинус/EMD между признаками Vision для семантической стабильности.
  - Latency offline-преобразования (`compression_latency_ms`).

## Методология

1. **V²Drop (arXiv:2509.01552)**
   - Реализовать PyTorch-скрипт, который удаляет малоинформативные токены из ViT-пайплайна.
   - Использовать публичный репозиторий (MIT) + адаптация под GigaChat (≈ViT-B/16).
   - Экспорт изображений, переданных в Vision, в формате npz с масками.

2. **AdaViT (arXiv:2112.07658)**
   - Настроить динамическое обрезание токенов на основе Layer-wise Importance.
   - Сравнить 30%, 50% sparsity.

3. **Token Merging (ToMe, arXiv:2210.09461)**
   - Использовать существующую имплементацию (`tome/torch`).
   - Оценить merge-factors 0.5 и 0.7.

## План PoC

| Этап | Шаги | Выходные артефакты |
|------|------|--------------------|
| A | Подготовка офлайн-бейзлайна (собрать `tokens_baseline.jsonl`, `ocr_baseline.jsonl`) | baseline отчёт, Prometheus snapshot |
| B | Применение V²Drop/AdaViT/ToMe (jupyter-скрипт `notebooks/vision_compression.ipynb`) | отчёт с метриками, графики |
| C | Сравнение результатов (CLIP, OCR, tokens) | `reports/VISION_COMPRESSION_POC.md`, таблица сравнения |
| D | Выбор кандидата для Wave D (если экономия ≥15% без потери OCR) | RFC для внедрения |

## Best Practices (Context7)

- Все результаты логировать в ClickHouse (`vision_compression_results`): колонки `tenant_id`, `method`, `tokens_before`, `tokens_after`, `ocr_wer`, `clip_score`.
- Включить фич-флаг `FEATURE_VISION_COMPRESSION_POC` в worker (только для sandbox).
- Прогонять PoC в `sandbox-worker` без влияния на прод.
- Сохранять исходные изображения в S3 bucket `vision-poc/` с TTL 7 дней.
- Для reproducibility фиксировать версии PyTorch, CLIP, tome.

## Риски и ограничители

- Возможная деградация OCR (особенно рукописный текст) — проверять WER/char-accuracy.
- Дополнительная латентность: если `compression_latency_ms > 40`, фича не проходит.
- Лицензии репозиториев (MIT/Apache2 только).

## Следующие шаги

1. Собрать baseline JSON (`python worker/scripts/export_vision_baseline.py`).
2. Реализовать notebook с имплементацией трёх методов (см. раздел «Методология»).
3. Сравнить результаты и заполнить таблицу в `reports/VISION_COMPRESSION_POC.md`.
4. Подготовить RFC по интеграции (если выгода подтверждена ≥15%).

