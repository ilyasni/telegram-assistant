# Система защит при разработке

## Обзор

Система защит обеспечивает безопасную разработку с защитой критичных файлов, автоматической валидацией конфигураций и предотвращением случайных изменений в продакшн-конфигурациях.

## Компоненты системы

### 1. Env-политика

#### Файлы
- `.env.schema.json` - JSON Schema для валидации переменных окружения
- `scripts/env-check.sh` - скрипт проверки .env по схеме
- `.env.example` - шаблон с обязательными переменными

#### Обязательные переменные
```bash
JWT_SECRET=your_jwt_secret_key_here
ANON_KEY=your_anon_key_here
SERVICE_KEY=your_service_key_here
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
QDRANT_URL=http://...
SUPABASE_HOST=your-domain.com
POSTGRES_DB=telegram_assistant
POSTGRES_PASSWORD=your_secure_password_here
DEFAULT_TENANT_ID=default-tenant
```

#### Защиты
```bash
# Запретить Git трекать локальный .env
echo ".env" >> .gitignore
git update-index --skip-worktree .env

# (опционально) сделать .env неизменяемым на уровне FS
sudo chattr +i .env      # снять: sudo chattr -i .env
```

### 2. Compose-защита

#### Файлы
- `scripts/compose-guard.sh` - проверка синтаксиса и обязательных сервисов
- `docker-compose.dev.yml` - override для разработки
- `docker-compose.yml` - базовый файл (защищён от изменений)

#### Обязательные сервисы
```bash
supabase-db kong rest auth storage redis qdrant api worker telethon-ingest
```

#### Защиты
```bash
# Зафиксировать рабочий compose от случайных изменений IDE
git update-index --skip-worktree docker-compose.yml

# (опц.) на время релиза:
sudo chattr +i docker-compose.yml
```

### 3. Git-политика

#### Pre-commit hook
- Запрещает коммит `.env` файлов
- Проверяет `docker-compose.yml` через `scripts/compose-guard.sh`
- Автоматическая установка через `scripts/setup-git-hooks.sh`

#### Ветки/PR
- Рабочая: `main` — защищённая (только PR)
- Разработка: `feature/*`
- Требования к PR: «зелёный» `scripts/env-check.sh` и `scripts/compose-guard.sh`

### 4. Makefile для разработки

#### Основные команды
```bash
make guard          # Проверка compose конфигурации
make env-check      # Валидация .env
make up-core        # Запуск ядра (Supabase, Redis, Qdrant)
make up-app         # Запуск приложения (API, Worker, Telethon)
make logs           # Просмотр логов сервисов
make smoke          # Smoke тесты доступности
```

### 5. Cursor/VSCode защита

#### Настройки (.vscode/settings.json)
```json
{
  "files.readonlyInclude": [
    "**/.env",
    "**/docker-compose.yml"
  ],
  "files.watcherExclude": {
    "**/.env": true
  },
  "search.exclude": {
    "**/.env": true,
    "**/docker-compose.yml": true,
    "**/Caddyfile": true,
    "**/supabase/volumes/api/kong.yml": true
  }
}
```

#### Запрещённые операции
- Никогда не делать «Replace in files» по критичным файлам
- Изменения только через override-файлы

### 6. Smoke-тесты

#### Файл: `scripts/smoke-e2e.sh`
- Ping Redis и Qdrant
- Проверка внешних Supabase путей (REST, AUTH, STORAGE)
- Тестовая вставка в БД

#### Запуск
```bash
make smoke
# или
./scripts/smoke-e2e.sh
```

### 7. CI/Checks

#### GitHub Actions (`.github/workflows/guard.yml`)
- Проверка compose конфигурации
- Валидация env схемы
- Автоматический запуск на PR

### 8. Документация восстановления

#### Файл: `docs/RECOVERY.md`
```bash
# Восстановление compose
git checkout -- docker-compose.yml

# Поиск рабочего коммита
git reflog

# Восстановление секретов
cp .env.example .env
# Заполнить значения из Supabase → Settings → API

# Снятие immutable флага
sudo chattr -i .env

# Перезапуск
make env-check guard up-core up-app
```

### 9. PR чек-лист

#### Файл: `docs/PR_CHECKLIST.md`
- [ ] `scripts/compose-guard.sh` — OK
- [ ] Базовые файлы — изменения осознанные, локально пройдены проверки
- [ ] `.env`/секреты — вне PR
- [ ] `scripts/env-check.sh` — OK локально
- [ ] Smoke тест (`make smoke`) — OK

## Workflow разработки

### Начальная настройка
```bash
# 1. Установка pre-commit hook
./scripts/setup-git-hooks.sh

# 2. Создание локального .env
cp .env.example .env
# Заполнить значения

# 3. Проверка конфигурации
make env-check guard

# 4. Запуск сервисов
make up-core up-app

# 5. Smoke тесты
make smoke
```

### Ежедневная работа
```bash
# Запуск ядра
make up-core

# Запуск приложения
make up-app

# Просмотр логов
make logs

# Проверка состояния
make smoke
```

### Эксперименты
```bash
# Использование override для экспериментов
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Или через Makefile с override
make up-core up-app
```

## Безопасность

### Защищённые файлы
- `.env` - переменные окружения
- `docker-compose.yml` - основная конфигурация
- `Caddyfile` - конфигурация прокси
- `supabase/volumes/api/kong.yml` - конфигурация API Gateway

### Защиты
- Pre-commit hooks против коммита секретов
- Readonly режим в редакторе
- Git skip-worktree для локальных изменений
- FS immutable флаги (опционально)

## Troubleshooting

### Проблемы с env
```bash
# Проверка схемы
make env-check

# Восстановление из примера
cp .env.example .env
```

### Проблемы с compose
```bash
# Проверка конфигурации
make guard

# Восстановление базового файла
git checkout -- docker-compose.yml
```

### Проблемы с hooks
```bash
# Переустановка hook
./scripts/setup-git-hooks.sh

# Проверка hook
ls -la .git/hooks/pre-commit
```

## Контекст7-маркеры

В коде используются маркеры для отслеживания архитектурных решений:

- `[C7-ID: ENV-SEC-003]` - Не трогать docker-compose.yml в дев — только через override
- `[C7-ID: NET-ROUTING-002]` - Caddy: исключаем Basic Auth для Supabase API путей
- `[C7-ID: SUPA-KEYS-001]` - ANON/SERVICE должны быть подписаны JWT_SECRET

## Best Practices

1. **Всегда используй override-файлы** для экспериментов
2. **Никогда не коммить .env** - только .env.example
3. **Проверяй compose** перед коммитом
4. **Используй smoke-тесты** для проверки готовности
5. **Документируй изменения** в критичных файлах
6. **Используй Context7-маркеры** для архитектурных решений
