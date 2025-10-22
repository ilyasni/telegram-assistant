# Makefile для Telegram Assistant
# Управление проектом и автоматизация задач

.PHONY: help diag monitor build up down restart logs clean test

# Цвета для вывода
BLUE=\033[0;34m
GREEN=\033[0;32m
YELLOW=\033[1;33m
RED=\033[0;31m
NC=\033[0m # No Color

# Помощь
help: ## Показать справку
	@echo "$(BLUE)Telegram Assistant - Makefile$(NC)"
	@echo
	@echo "$(YELLOW)Доступные команды:$(NC)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Диагностика и мониторинг
diag: ## Проверка состояния всех сервисов
	@echo "$(BLUE)Запуск диагностики...$(NC)"
	@bash scripts/diagnostic.sh

monitor: ## Мониторинг логов в реальном времени
	@echo "$(BLUE)Запуск мониторинга логов...$(NC)"
	@bash scripts/monitor.sh

# Docker Compose команды
build: ## Сборка всех образов
	@echo "$(BLUE)Сборка образов...$(NC)"
	docker compose build

up: ## Запуск всех сервисов
	@echo "$(BLUE)Запуск сервисов...$(NC)"
	docker compose --profile core up -d

up-analytics: ## Запуск с аналитикой (Grafana)
	@echo "$(BLUE)Запуск сервисов с аналитикой...$(NC)"
	docker compose --profile core --profile analytics up -d

up-rag: ## Запуск с RAG (векторный поиск)
	@echo "$(BLUE)Запуск сервисов с RAG...$(NC)"
	docker compose --profile core --profile rag up -d

up-all: ## Запуск всех профилей
	@echo "$(BLUE)Запуск всех сервисов...$(NC)"
	docker compose --profile core --profile analytics --profile rag up -d

down: ## Остановка всех сервисов
	@echo "$(BLUE)Остановка сервисов...$(NC)"
	docker compose down

restart: ## Перезапуск всех сервисов
	@echo "$(BLUE)Перезапуск сервисов...$(NC)"
	docker compose restart

# Логи
logs: ## Просмотр логов всех сервисов
	@echo "$(BLUE)Просмотр логов...$(NC)"
	docker compose logs --tail=100 -f

logs-api: ## Логи API сервиса
	docker compose logs --tail=100 -f api

logs-telethon: ## Логи Telethon сервиса
	docker compose logs --tail=100 -f telethon-ingest

logs-redis: ## Логи Redis
	docker compose logs --tail=100 -f redis

logs-db: ## Логи PostgreSQL
	docker compose logs --tail=100 -f supabase-db

logs-qdrant: ## Логи Qdrant
	docker compose logs --tail=100 -f qdrant

logs-caddy: ## Логи Caddy
	docker compose logs --tail=100 -f caddy

# Управление данными
clean: ## Очистка volumes и данных
	@echo "$(RED)ВНИМАНИЕ: Это удалит все данные!$(NC)"
	@read -p "Вы уверены? (y/N): " confirm && [ "$$confirm" = "y" ]
	docker compose down -v
	docker system prune -f

clean-logs: ## Очистка логов
	@echo "$(BLUE)Очистка логов...$(NC)"
	docker compose logs --tail=0 > /dev/null 2>&1 || true

# Тестирование
test: ## Запуск тестов
	@echo "$(BLUE)Запуск тестов...$(NC)"
	@echo "$(YELLOW)Тесты пока не реализованы$(NC)"

test-e2e: ## E2E тесты
	@echo "$(BLUE)Запуск E2E тестов...$(NC)"
	@echo "$(YELLOW)E2E тесты пока не реализованы$(NC)"

# Разработка
dev: ## Запуск в режиме разработки
	@echo "$(BLUE)Запуск в режиме разработки...$(NC)"
	docker compose --profile core up -d
	@echo "$(GREEN)Сервисы запущены. Используйте 'make monitor' для просмотра логов$(NC)"

dev-build: ## Сборка и запуск в режиме разработки
	@echo "$(BLUE)Сборка и запуск в режиме разработки...$(NC)"
	docker compose build
	docker compose --profile core up -d

# Администрирование
admin-invites: ## Управление инвайтами (CLI)
	@echo "$(BLUE)CLI для управления инвайтами...$(NC)"
	@echo "$(YELLOW)Используйте: python scripts/invites_cli.py --help$(NC)"

# Проверка состояния
status: ## Статус всех сервисов
	@echo "$(BLUE)Статус сервисов:$(NC)"
	docker compose ps

health: ## Проверка health endpoints
	@echo "$(BLUE)Проверка health endpoints:$(NC)"
	@echo "API: $$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health || echo 'недоступен')"
	@echo "Telethon: $$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8011/health || echo 'недоступен')"
	@echo "Qdrant: $$(curl -s -o /dev/null -w '%{http_code}' http://localhost:6333/health || echo 'недоступен')"

# Обновление
update: ## Обновление образов
	@echo "$(BLUE)Обновление образов...$(NC)"
	docker compose pull
	docker compose up -d

# Резервное копирование
backup: ## Создание резервной копии
	@echo "$(BLUE)Создание резервной копии...$(NC)"
	@mkdir -p backups
	docker compose exec supabase-db pg_dump -U postgres telegram_assistant > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)Резервная копия создана в backups/$(NC)"

# Установка зависимостей
install: ## Установка зависимостей для разработки
	@echo "$(BLUE)Установка зависимостей...$(NC)"
	@echo "$(YELLOW)Убедитесь, что Python 3.11+ установлен$(NC)"
	pip install -r api/requirements.txt
	pip install -r telethon-ingest/requirements.txt
	pip install -r worker/requirements.txt

# Линтинг
lint: ## Проверка кода линтерами
	@echo "$(BLUE)Проверка кода...$(NC)"
	@echo "$(YELLOW)Линтеры пока не настроены$(NC)"

# Форматирование
format: ## Форматирование кода
	@echo "$(BLUE)Форматирование кода...$(NC)"
	@echo "$(YELLOW)Форматтеры пока не настроены$(NC)"

# По умолчанию показываем справку
.DEFAULT_GOAL := help
