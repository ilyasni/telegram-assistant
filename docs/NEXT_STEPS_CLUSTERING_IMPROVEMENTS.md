# Следующие шаги: Улучшение кластеризации трендов

## Реализовано

✅ Все 4 этапа реализации завершены:
- Этап 1: Динамический порог когерентности
- Этап 2: LLM Topic Gate
- Этап 3: Topic-Coherence Agent
- Этап 4: Graph-RAG валидация и Drift Detector

✅ Добавлены метрики Prometheus для мониторинга

## Шаг 1: Перезапуск Worker

```bash
# Перезапустить worker для применения изменений
docker compose restart worker

# Проверить статус
docker compose ps worker

# Следить за логами во время запуска
docker compose logs -f worker | grep -E "TrendDetectionWorker|CoherenceAgent|GraphService|DriftDetector|initialized"
```

**Ожидаемый результат:**
- `TrendDetectionWorker initialized`
- `TrendCoherenceAgent initialized in TrendDetectionWorker`
- `GraphService initialized in TrendDetectionWorker` (если Neo4j доступен)
- `DriftDetectorAgent initialized in TrendDetectionWorker`

## Шаг 2: Проверка инициализации компонентов

```bash
# Проверить логи на ошибки инициализации
docker compose logs worker | grep -iE "error|failed|exception" | tail -20

# Проверить успешную инициализацию всех компонентов
docker compose logs worker | grep -iE "initialized|ready" | grep -E "TrendDetectionWorker|CoherenceAgent|GraphService|DriftDetector" | tail -10
```

**Что проверить:**
- Все компоненты успешно инициализированы
- Нет ошибок подключения к БД, Qdrant, Neo4j
- Нет ошибок импорта модулей

## Шаг 3: Проверка работы валидаторов

```bash
# Следить за логами работы валидаторов в реальном времени
docker compose logs -f worker | grep -E "topic_gate|coherence_agent|graph_validator|drift_detector|rejected"

# Проверить случаи отклонения постов
docker compose logs worker | grep "rejected" | tail -20
```

**Ожидаемое поведение:**
- Динамический порог применяется для разных размеров кластеров
- LLM Topic Gate вызывается для больших кластеров (>= 3 постов)
- Coherence Agent проверяет тематическую когерентность
- Graph Validator проверяет связность тем через Neo4j
- Drift Detector логирует случаи дрейфа темы (для кластеров >= 5 постов)

## Шаг 4: Мониторинг метрик Prometheus

```bash
# Проверить доступность метрик
curl -s http://localhost:9090/api/v1/query?query=trend_clustering_rejected_total | jq

# Проверить метрики отклоненных постов по причинам
curl -s "http://localhost:9090/api/v1/query?query=trend_clustering_rejected_total" | jq '.data.result[] | {reason: .metric.reason, value: .value[1]}'

# Проверить распределение coherence scores
curl -s "http://localhost:9090/api/v1/query?query=trend_clustering_coherence_score_histogram" | jq

# Проверить размеры кластеров
curl -s "http://localhost:9090/api/v1/query?query=trend_clustering_cluster_size_histogram" | jq

# Проверить латентность LLM Topic Gate
curl -s "http://localhost:9090/api/v1/query?query=trend_clustering_llm_gate_latency_seconds" | jq
```

**Ключевые метрики для мониторинга:**
- `trend_clustering_rejected_total{reason="dynamic_threshold"}` - отклонено из-за динамического порога
- `trend_clustering_rejected_total{reason="topic_gate"}` - отклонено LLM Topic Gate
- `trend_clustering_rejected_total{reason="coherence_agent"}` - отклонено Coherence Agent
- `trend_clustering_rejected_total{reason="graph_validation"}` - отклонено Graph Validator
- `trend_clustering_rejected_total{reason="drift_detection"}` - обнаружен дрейф темы

## Шаг 5: Проверка работы на реальных данных

```bash
# Запустить обнаружение трендов вручную (если есть API endpoint)
curl -X POST http://localhost:8000/api/trends/detect

# Проверить результат через бот
# Или через API
curl -s "http://localhost:8000/api/trends/clusters?status=stable&page=1&page_size=5" | jq '.clusters[] | {primary_topic, coherence_score, window_mentions, is_generic, quality_score}'

# Проверить, что generic-тренды не появляются
curl -s "http://localhost:8000/api/trends/clusters?status=stable&page=1&page_size=10" | jq '.clusters[] | select(.primary_topic | test("^(которые|начали|компания|первый)$"))'
```

**Ожидаемый результат:**
- В списке трендов нет generic-слов ("которые", "начали", "компания" и т.д.)
- Тренды тематически однородны
- Coherence scores выше порогов для соответствующих размеров кластеров

## Шаг 6: Настройка параметров (опционально)

Если валидация слишком строгая или слишком мягкая, можно настроить параметры:

```bash
# Редактировать .env файл или переменные окружения в docker-compose.yml
```

**Параметры для настройки:**

1. **Динамический порог** (в коде, может быть вынесен в env):
   - Размер кластера 1-2: 0.55
   - Размер кластера 3-5: 0.65
   - Размер кластера 5-10: 0.70
   - Размер кластера >10: 0.75

2. **LLM Topic Gate:**
   - `TREND_TOPIC_GATE_ENABLED=true` - включить/выключить
   - `TREND_TOPIC_GATE_THRESHOLD=0.70` - порог similarity для включения проверки
   - `TREND_TOPIC_GATE_CLUSTER_SIZE=3` - минимальный размер кластера для проверки

3. **Coherence Agent:**
   - `TREND_COHERENCE_AGENT_ENABLED=true` - включить/выключить
   - `TREND_COHERENCE_AGENT_THRESHOLD=0.65` - минимальный similarity для включения

4. **Graph Validator:**
   - `TREND_GRAPH_VALIDATION_ENABLED=true` - включить/выключить

5. **Drift Detector:**
   - `TREND_DRIFT_DETECTION_ENABLED=true` - включить/выключить
   - `TREND_DRIFT_THRESHOLD=0.05` - порог обнаружения дрейфа (0.0-1.0)

## Шаг 7: Мониторинг и анализ

### Ежедневная проверка метрик

```bash
# Скрипт для проверки метрик
cat > /tmp/check_clustering_metrics.sh << 'EOF'
#!/bin/bash
echo "=== Метрики отклоненных постов ==="
curl -s "http://localhost:9090/api/v1/query?query=trend_clustering_rejected_total" | \
  jq '.data.result[] | "\(.metric.reason): \(.value[1])"'

echo -e "\n=== Средний coherence score ==="
curl -s "http://localhost:9090/api/v1/query?query=avg(trend_clustering_coherence_score_histogram)" | \
  jq '.data.result[0].value[1]'

echo -e "\n=== Средний размер кластера ==="
curl -s "http://localhost:9090/api/v1/query?query=avg(trend_clustering_cluster_size_histogram)" | \
  jq '.data.result[0].value[1]'

echo -e "\n=== Средняя латентность LLM Topic Gate ==="
curl -s "http://localhost:9090/api/v1/query?query=avg(trend_clustering_llm_gate_latency_seconds)" | \
  jq '.data.result[0].value[1]'
EOF

chmod +x /tmp/check_clustering_metrics.sh
/tmp/check_clustering_metrics.sh
```

### Проверка качества трендов

```bash
# Проверить, что в БД нет generic-трендов со статусом stable
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT primary_topic, is_generic, quality_score, window_mentions, coherence_score
FROM trend_clusters
WHERE status = 'stable' AND is_generic = true
ORDER BY last_activity_at DESC
LIMIT 10;
"

# Проверить распределение coherence scores
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    CASE 
        WHEN coherence_score < 0.55 THEN '<0.55'
        WHEN coherence_score < 0.65 THEN '0.55-0.65'
        WHEN coherence_score < 0.70 THEN '0.65-0.70'
        WHEN coherence_score < 0.75 THEN '0.70-0.75'
        ELSE '>=0.75'
    END as coherence_range,
    COUNT(*) as clusters_count
FROM trend_clusters
WHERE status IN ('emerging', 'stable')
GROUP BY coherence_range
ORDER BY coherence_range;
"
```

## Шаг 8: Troubleshooting

### Проблема: Worker не запускается

```bash
# Проверить логи на ошибки импорта
docker compose logs worker | grep -iE "import.*error|module.*not.*found" | tail -10

# Проверить, что все файлы на месте
docker compose exec worker ls -la /app/trends_coherence_agent.py /app/trends_graph_validator.py /app/trends_drift_detector.py
```

### Проблема: Coherence Agent не работает

```bash
# Проверить логи вызовов LLM
docker compose logs worker | grep -E "coherence_agent|_call_coherence_llm" | tail -20

# Проверить доступность GigaChat
curl -s http://localhost:8000/health | jq
```

### Проблема: Graph Validator не работает

```bash
# Проверить доступность Neo4j
docker compose exec neo4j cypher-shell -u neo4j -p neo4j123 "RETURN 1"

# Проверить логи инициализации GraphService
docker compose logs worker | grep -iE "graphservice|neo4j" | tail -10
```

### Проблема: Слишком много отклонений

```bash
# Проверить метрики отклонений
curl -s "http://localhost:9090/api/v1/query?query=rate(trend_clustering_rejected_total[5m])" | jq

# Если слишком много отклонений, можно временно снизить пороги:
# - TREND_TOPIC_GATE_THRESHOLD=0.65 (вместо 0.70)
# - TREND_COHERENCE_AGENT_THRESHOLD=0.60 (вместо 0.65)
```

## Шаг 9: Оценка эффективности

Через несколько дней работы проверить:

1. **Снижение количества generic-трендов:**
   ```bash
   # Сравнить до/после (если есть исторические данные)
   docker compose exec -T supabase-db psql -U postgres -d postgres -c "
   SELECT 
       DATE(created_at) as date,
       COUNT(*) FILTER (WHERE is_generic = true) as generic_count,
       COUNT(*) FILTER (WHERE is_generic = false) as valid_count
   FROM trend_clusters
   WHERE created_at >= NOW() - INTERVAL '7 days'
   GROUP BY DATE(created_at)
   ORDER BY date;
   "
   ```

2. **Улучшение качества трендов:**
   - Средний quality_score должен быть выше
   - Больше трендов с summary
   - Меньше однословных трендов

3. **Производительность:**
   - Латентность LLM Topic Gate должна быть приемлемой (< 5 сек)
   - Количество отклонений должно быть разумным (не слишком много, не слишком мало)

## Контрольный чеклист

- [ ] Worker перезапущен без ошибок
- [ ] Все компоненты инициализированы (Coherence Agent, Graph Service, Drift Detector)
- [ ] Метрики Prometheus доступны и обновляются
- [ ] В логах видны случаи работы валидаторов
- [ ] Generic-тренды больше не появляются в стабильных трендах
- [ ] Тренды тематически однородны
- [ ] Производительность приемлемая (латентность LLM не критична)

