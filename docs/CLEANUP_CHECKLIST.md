# Чеклист очистки перед коммитом

[C7-ID: CODE-CLEANUP-032] Быстрая проверка перед коммитом

## ✅ Обязательные проверки

### 1. Code Quality

```bash
# Все проверки
make quality

# Или по отдельности:
make lint           # ✅ Должен пройти
make format-check   # ✅ Должен пройти
make dead-code      # ⚠️  Проверить результаты
make check-duplicates  # ⚠️  Проверить результаты
```

### 2. Pre-commit Hooks

```bash
# Проверить что hooks установлены
pre-commit run --all-files

# Если не установлены:
make pre-commit-install
```

### 3. Импорты

- ❌ НЕ импортировать из `/legacy/`
- ✅ Использовать `shared.feature_flags` вместо локальных модулей
- ✅ Проверить архитектурные границы: `lint-imports`

### 4. Deprecated код

```bash
# Проверить использование deprecated модулей
grep -r "from worker.shared.s3_storage" .
grep -r "from worker.health_check" .
grep -r "from worker.feature_flags" .  # Должен использовать shared
```

### 5. Тесты

- ✅ Все тесты в `tests/` (unit/, integration/, e2e/)
- ❌ НЕ создавать `test_*.py` в корне проекта

## 📋 Опциональные проверки

### Инвентаризация

```bash
# Генерация отчётов (еженедельно)
make inventory
```

### Legacy cleanup

```bash
# Проверка legacy файлов (перед удалением)
make clean-legacy
```

## 🚨 Критичные ошибки

Если видите эти ошибки — исправьте перед коммитом:

- `ImportError: cannot import from legacy/` → Используйте правильный модуль
- `worker.feature_flags is deprecated` → Мигрируйте на `shared.feature_flags`
- `Architecture violation: api imports worker` → Исправьте импорт

## ✅ Итоговый чеклист

Перед `git commit`:

- [ ] `make quality` проходит
- [ ] Pre-commit hooks установлены и работают
- [ ] Нет импортов из legacy/
- [ ] Feature flags используют shared.feature_flags
- [ ] Тесты проходят
- [ ] Нет новых дубликатов (проверить через `make check-duplicates`)

