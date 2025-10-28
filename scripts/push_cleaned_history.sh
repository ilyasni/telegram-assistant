#!/bin/bash
# Скрипт для безопасного force push очищенной истории на GitHub
# ВНИМАНИЕ: Это перезапишет историю на GitHub!

set -euo pipefail

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка, что мы на правильной ветке
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
    log_warning "Вы не на ветке main. Текущая ветка: $CURRENT_BRANCH"
    read -p "Продолжить? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Проверка удалённого репозитория
REMOTE_URL=$(git remote get-url origin)
log_info "Удалённый репозиторий: $REMOTE_URL"

# Проверка изменений
log_info "Проверка состояния репозитория..."
LOCAL_COMMIT=$(git rev-parse HEAD)
REMOTE_COMMIT=$(git rev-parse origin/main 2>/dev/null || echo "")

if [ -z "$REMOTE_COMMIT" ]; then
    log_warning "Не удалось получить состояние origin/main"
    log_info "Будет выполнена первая отправка на GitHub"
else
    log_info "Локальный HEAD:  ${LOCAL_COMMIT:0:7}"
    log_info "Remote origin/main: ${REMOTE_COMMIT:0:7}"
    
    if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
        log_warning "Локальная и удалённая ветки совпадают!"
        log_info "Возможно, история уже очищена на GitHub"
        exit 0
    fi
fi

# Предупреждение
log_warning "=========================================="
log_warning "ВНИМАНИЕ: Force push перезапишет историю!"
log_warning "=========================================="
log_warning ""
log_warning "Это действие:"
log_warning "  - Удалит последний коммит с чувствительными данными на GitHub"
log_warning "  - Перезапишет всю историю очищенными коммитами"
log_warning "  - Требует переклонирования для всех разработчиков"
log_warning ""
log_warning "Убедитесь, что:"
log_warning "  1. Все чувствительные данные пересозданы"
log_warning "  2. Все разработчики предупреждены"
log_warning "  3. У вас есть резервная копия (создана автоматически)"
log_warning ""

read -p "Вы уверены? Введите 'yes' для продолжения: " -r
if [ "$REPLY" != "yes" ]; then
    log_info "Операция отменена"
    exit 1
fi

# Force push
log_info "Выполнение force push на GitHub..."

log_info "Отправка основной ветки..."
if git push origin --force main; then
    log_success "Ветка main успешно отправлена!"
else
    log_error "Ошибка при отправке ветки main"
    exit 1
fi

# Проверка наличия других веток
OTHER_BRANCHES=$(git branch -r | grep -v "origin/main$" | grep "origin/" | sed 's|origin/||' | tr '\n' ' ')
if [ -n "$OTHER_BRANCHES" ]; then
    log_info "Обнаружены другие ветки: $OTHER_BRANCHES"
    read -p "Отправить все ветки? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Отправка всех веток..."
        git push origin --force --all
        log_success "Все ветки отправлены!"
    fi
fi

# Проверка наличия тегов
TAGS=$(git tag -l)
if [ -n "$TAGS" ]; then
    log_info "Обнаружены теги: $TAGS"
    read -p "Отправить все теги? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Отправка всех тегов..."
        git push origin --force --tags
        log_success "Все теги отправлены!"
    fi
fi

log_success "=========================================="
log_success "Force push успешно выполнен!"
log_success "=========================================="
log_info ""
log_info "Следующие шаги:"
log_info "  1. Проверьте GitHub: https://github.com/ilyasni/telegram-assistant"
log_info "  2. Уведомите всех разработчиков о необходимости переклонирования"
log_info "  3. Убедитесь, что чувствительные данные действительно удалены"
log_info ""
log_warning "Для переклонирования:"
log_warning "  git clone https://github.com/ilyasni/telegram-assistant.git"

