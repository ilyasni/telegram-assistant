# Настройка GitHub аутентификации

## Проблема
```
Failed to push branch. Configure GitHub auth and try again.
```

## Решение 1: Personal Access Token (PAT) для HTTPS

### Шаг 1: Создать токен на GitHub
1. Перейти: https://github.com/settings/tokens
2. Нажать "Generate new token" → "Generate new token (classic)"
3. Настроить:
   - **Note**: `telegram-assistant-dev`
   - **Expiration**: на выбор (90 дней / 1 год / без срока)
   - **Scopes**: `repo` (полный доступ к репозиториям)
4. Скопировать токен (показывается только один раз!)

### Шаг 2: Настроить Git credential helper

```bash
# Использовать токен в URL (один раз для push)
git push https://<TOKEN>@github.com/ilyasni/telegram-assistant.git

# Или настроить credential helper для автоматического использования
git config --global credential.helper store
# При следующем push ввести: username = ваш_github_username, password = TOKEN

# Или использовать GitHub CLI (рекомендуется)
gh auth login
```

### Шаг 3: Push ветки

```bash
cd /opt/telegram-assistant
git push origin 2025-10-31-7xy7-7dfe9
```

---

## Решение 2: Переключиться на SSH

### Шаг 1: Проверить наличие SSH ключа

```bash
ls -la ~/.ssh/id_*.pub
```

Если ключа нет - создать:

```bash
ssh-keygen -t ed25519 -C "telegram-assistant@example.com"
# Нажать Enter для всех вопросов
```

### Шаг 2: Добавить ключ на GitHub

```bash
cat ~/.ssh/id_ed25519.pub
# Скопировать вывод и добавить на: https://github.com/settings/keys
```

### Шаг 3: Переключить remote на SSH

```bash
cd /opt/telegram-assistant
git remote set-url origin git@github.com:ilyasni/telegram-assistant.git
git remote -v  # Проверить

# Проверить подключение
ssh -T git@github.com
```

### Шаг 4: Push

```bash
git push origin 2025-10-31-7xy7-7dfe9
```

---

## Решение 3: GitHub CLI (самый простой)

```bash
# Установить GitHub CLI (если нет)
# Ubuntu/Debian:
sudo apt install gh

# Или через snap:
sudo snap install gh

# Авторизоваться
gh auth login
# Выбрать: GitHub.com → HTTPS → Authenticate Git with your GitHub credentials? Yes

# Push
git push origin 2025-10-31-7xy7-7dfe9
```

---

## Проверка

```bash
# Проверить remote URL
git remote -v

# Проверить доступ
git ls-remote origin HEAD

# Попробовать push
git push origin 2025-10-31-7xy7-7dfe9
```

## Troubleshooting

### Ошибка: "remote: Permission denied"
- PAT истек или недостаточно прав
- Пересоздать токен с правом `repo`

### Ошибка: "Could not read from remote repository"
- Проверить SSH подключение: `ssh -T git@github.com`
- Проверить SSH ключ добавлен на GitHub

### Ошибка: "Authentication failed"
- Очистить кеш credentials:
  ```bash
  git credential-cache exit
  git config --global --unset credential.helper
  ```
- Повторить авторизацию

