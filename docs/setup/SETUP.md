# Настройка репозитория

## Создание GitHub репозитория

### Вариант 1: Через GitHub CLI (рекомендуется)
```bash
# Установка GitHub CLI (если не установлен)
# Ubuntu/Debian:
sudo apt install gh

# Создание репозитория
gh repo create telegram-assistant \
  --description "Telegram Channel Parser Bot - Event-driven microservices architecture with LangChain" \
  --public \
  --source=. \
  --remote=origin \
  --push
```

### Вариант 2: Через веб-интерфейс GitHub
1. Перейти на https://github.com/new
2. Название репозитория: `telegram-assistant`
3. Описание: `Telegram Channel Parser Bot - Event-driven microservices architecture with LangChain`
4. Выбрать `Public`
5. НЕ инициализировать с README (у нас уже есть)
6. Создать репозиторий
7. Выполнить команды:

```bash
# Добавить remote origin
git remote add origin https://github.com/YOUR_USERNAME/telegram-assistant.git

# Push в GitHub
git push -u origin main
```

## Настройка локального репозитория

### Инициализация (уже выполнено)
```bash
# Git репозиторий уже инициализирован
# Основная ветка: main
# Первый коммит: fca10de
```

### Проверка статуса
```bash
# Проверить статус
git status

# Проверить историю
git log --oneline

# Проверить remote
git remote -v
```

## Структура проекта

```
telegram-assistant/
├── .cursor/                 # Правила Cursor для разработки
│   ├── rules/              # Модульные правила
│   └── mcp.json           # MCP конфигурация
├── docs/                   # Документация
│   ├── ARCHITECTURE_PRINCIPLES.md  # Новая архитектура
│   ├── MIGRATION_GUIDE.md          # Руководство по миграции
│   └── OLD_*              # Документация старой версии
├── api/                    # FastAPI Gateway (пустая)
├── worker/                 # Worker Service (пустая)
├── telethon-ingest/        # Telegram Ingestion (пустая)
├── postgres/              # PostgreSQL конфигурация
├── redis/                 # Redis конфигурация
├── qdrant/                # Qdrant конфигурация
├── neo4j/                 # Neo4j конфигурация
├── grafana/               # Grafana мониторинг
├── docker-compose.yml     # Docker Compose конфигурация
├── README.md              # Основная документация
├── .gitignore            # Git ignore правила
└── SETUP.md               # Этот файл
```

## Следующие шаги

### 1. Публикация в GitHub
```bash
# Если используете GitHub CLI
gh repo create telegram-assistant --public --source=. --remote=origin --push

# Если создаете через веб-интерфейс
git remote add origin https://github.com/YOUR_USERNAME/telegram-assistant.git
git push -u origin main
```

### 2. Настройка CI/CD
- Добавить GitHub Actions workflow
- Настроить автоматические тесты
- Настроить Docker image building

### 3. Настройка защиты веток
- Защитить main ветку
- Требовать PR для изменений
- Настроить автоматические проверки

### 4. Настройка Issues и Projects
- Создать шаблоны для Issues
- Настроить Project boards
- Добавить labels для категоризации

## Команды для работы с репозиторием

### Основные команды
```bash
# Проверить статус
git status

# Добавить изменения
git add .

# Сделать коммит
git commit -m "Описание изменений"

# Push в GitHub
git push origin main

# Pull изменений
git pull origin main
```

### Работа с ветками
```bash
# Создать новую ветку
git checkout -b feature/new-feature

# Переключиться на ветку
git checkout main

# Слить ветку
git merge feature/new-feature

# Удалить ветку
git branch -d feature/new-feature
```

## Troubleshooting

### Проблемы с аутентификацией
```bash
# Настроить SSH ключи
ssh-keygen -t ed25519 -C "your_email@example.com"
ssh-add ~/.ssh/id_ed25519

# Добавить ключ в GitHub
cat ~/.ssh/id_ed25519.pub
```

### Проблемы с правами доступа
```bash
# Проверить remote URL
git remote -v

# Изменить на SSH
git remote set-url origin git@github.com:USERNAME/telegram-assistant.git
```

## Заключение

Репозиторий готов к работе! Основная структура создана, документация добавлена, Git инициализирован. Следующий шаг — публикация в GitHub и начало разработки согласно roadmap.
