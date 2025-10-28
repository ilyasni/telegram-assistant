#!/bin/bash
# Скрипт для очистки истории Git от чувствительных данных
# ВНИМАНИЕ: Это переписывает историю Git! Выполнять только если уверены!

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

# Проверка, что мы в git репозитории
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    log_error "Не находимся в git репозитории!"
    exit 1
fi

# Проверка, что нет незакоммиченных изменений (кроме .env)
log_info "Проверка рабочей директории..."
if [ -n "$(git status --porcelain | grep -v '^\?\?')" ]; then
    log_warning "Есть незакоммиченные изменения!"
    log_info "Выполните 'git status' для просмотра"
    read -p "Продолжить? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Резервная копия
BACKUP_BRANCH="backup-before-cleanup-$(date +%Y%m%d_%H%M%S)"
log_info "Создание резервной ветки: ${BACKUP_BRANCH}"
git branch "${BACKUP_BRANCH}" || log_warning "Не удалось создать резервную ветку"

# Список чувствительных данных для удаления
SENSITIVE_PATTERNS=(
    "N2MwNTA0NGMtZTM4Yy00YjRhLTliZjEtYTI5YzVmMWE4ZWMyOmRmM2Q3MWY1LTI2ZDItNDA2MS04NzVjLTIyYzNkM2YwMWRjMg=="
    "N2MwNTA0NGMtZTM4Yy00YjRhLTliZjEtYTI5YzVmMWE4ZWMyOjU3MWIwZTM1LWJlMDAtNDU3Yi1hYzkwLThlN2NlYTU2NWE4Yw=="
)

# Функция для замены чувствительных данных
clean_file() {
    local file="$1"
    
    # Замена конкретных credentials на placeholder
    for pattern in "${SENSITIVE_PATTERNS[@]}"; do
        if grep -q "$pattern" "$file" 2>/dev/null; then
            log_info "Найдены чувствительные данные в $file, заменяем..."
            sed -i "s|${pattern}|your_gigachat_credentials_base64_here|g" "$file"
        fi
    done
    
    # Замена токенов доступа (если есть в формате eyJ...)
    if grep -q "eyJjdHkiOiJqd3QiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiUlNBLU9BRVAtMjU2In0" "$file" 2>/dev/null; then
        log_info "Найден access token в $file, заменяем..."
        sed -i 's|eyJjdHkiOiJqd3QiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiUlNBLU9BRVAtMjU2In0.*|your_access_token_here|g' "$file"
    fi
}

# Экспорт функции для использования в filter-branch
export -f clean_file
export SENSITIVE_PATTERNS

log_info "Начало очистки истории Git от чувствительных данных..."
log_warning "Это может занять некоторое время..."

# Используем git filter-branch для очистки истории
# Используем tree-filter для замены чувствительных данных во всех файлах
log_info "Обработка истории коммитов..."

git filter-branch --force --tree-filter '
    # Функция замены чувствительных данных
    replace_sensitive() {
        local file="$1"
        if [ -f "$file" ]; then
            # Замена GIGACHAT_CREDENTIALS
            sed -i "s|N2MwNTA0NGMtZTM4Yy00YjRhLTliZjEtYTI5YzVmMWE4ZWMyOmRmM2Q3MWY1LTI2ZDItNDA2MS04NzVjLTIyYzNkM2YwMWRjMg==|your_gigachat_credentials_base64_here|g" "$file" 2>/dev/null || true
            sed -i "s|N2MwNTA0NGMtZTM4Yy00YjRhLTliZjEtYTI5YzVmMWE4ZWMyOjU3MWIwZTM1LWJlMDAtNDU3Yi1hYzkwLThlN2NlYTU2NWE4Yw==|your_gigachat_credentials_base64_here|g" "$file" 2>/dev/null || true
            # Замена access tokens
            sed -i "s|eyJjdHkiOiJqd3QiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwiYWxnIjoiUlNBLU9BRVAtMjU2In0[^\"]*|your_access_token_here|g" "$file" 2>/dev/null || true
        fi
    }
    
    # Обрабатываем файлы, которые могут содержать чувствительные данные
    for file in env.example .env.example docs/GIGACHAT_SUCCESS.md gpt2giga-proxy/test_credentials.py; do
        if [ -f "$file" ]; then
            replace_sensitive "$file"
        fi
    done
    
    # Ищем и заменяем во всех .md и .example файлах
    find . -type f \( -name "*.md" -o -name "*.example" -o -name "*.py" \) ! -path "./.git/*" -exec grep -l "N2MwNTA0NGMtZTM4Yy00YjRhLTliZjEtYTI5YzVmMWE4ZWMy" {} \; 2>/dev/null | while read -r file; do
        replace_sensitive "$file"
    done || true
' --prune-empty --tag-name-filter cat -- --all

log_success "Очистка истории завершена!"

log_info "Очистка резервных копий filter-branch..."
git for-each-ref --format="%(refname)" refs/original/ | xargs -n 1 git update-ref -d 2>/dev/null || true

log_info "Очистка reflog..."
git reflog expire --expire=now --all
git gc --prune=now --aggressive

log_success "Git история успешно очищена!"
log_info "Резервная ветка сохранена: ${BACKUP_BRANCH}"
log_warning "Для применения изменений на GitHub выполните:"
log_warning "  git push origin --force --all"
log_warning "  git push origin --force --tags"

