# Настройка SSH ключей для GitHub

## Шаг 1: Проверить существующие SSH ключи

```bash
ls -la ~/.ssh/id_*.pub
```

Если ключи есть - можно использовать существующий или создать новый.

## Шаг 2: Создать новый SSH ключ (если нет)

```bash
# Создать ключ типа ed25519 (рекомендуется)
ssh-keygen -t ed25519 -C "telegram-assistant@$(hostname)"

# Или использовать RSA (если ed25519 не поддерживается)
ssh-keygen -t rsa -b 4096 -C "telegram-assistant@$(hostname)"
```

**Во время создания:**
- Нажать `Enter` для сохранения в `~/.ssh/id_ed25519` (или `~/.ssh/id_rsa`)
- Ввести passphrase (опционально, но рекомендуется для безопасности) или нажать `Enter` для пропуска

## Шаг 3: Запустить SSH agent и добавить ключ

```bash
# Запустить SSH agent
eval "$(ssh-agent -s)"

# Добавить ключ в agent
ssh-add ~/.ssh/id_ed25519
# или для RSA:
# ssh-add ~/.ssh/id_rsa

# Если ключ защищен passphrase, нужно будет его ввести
```

## Шаг 4: Скопировать публичный ключ

```bash
# Показать публичный ключ
cat ~/.ssh/id_ed25519.pub
# или для RSA:
# cat ~/.ssh/id_rsa.pub
```

**Скопировать весь вывод** (начинается с `ssh-ed25519` или `ssh-rsa`)

## Шаг 5: Добавить ключ на GitHub

1. Перейти: https://github.com/settings/keys
2. Нажать **"New SSH key"**
3. Заполнить:
   - **Title**: `Telegram Assistant Server` (или любое понятное имя)
   - **Key type**: `Authentication Key`
   - **Key**: Вставить скопированный публичный ключ
4. Нажать **"Add SSH key"**
5. Подтвердить пароль GitHub (если требуется)

## Шаг 6: Проверить подключение

```bash
ssh -T git@github.com
```

**Ожидаемый результат:**
```
Hi ilyasni! You've successfully authenticated, but GitHub does not provide shell access.
```

## Шаг 7: Переключить Git remote на SSH

```bash
cd /opt/telegram-assistant

# Проверить текущий remote
git remote -v

# Переключить на SSH
git remote set-url origin git@github.com:ilyasni/telegram-assistant.git

# Проверить изменение
git remote -v
```

## Шаг 8: Протестировать push

```bash
# Попробовать push
git push origin 2025-10-31-7xy7-7dfe9
```

---

## Автоматическая настройка (скрипт)

Можно запустить этот скрипт для автоматической настройки:

```bash
#!/bin/bash
set -e

echo "=== Проверка существующих SSH ключей ==="
if ls ~/.ssh/id_*.pub 1> /dev/null 2>&1; then
    echo "Найден SSH ключ:"
    ls -la ~/.ssh/id_*.pub
    read -p "Использовать существующий ключ? (y/n): " use_existing
    if [ "$use_existing" != "y" ]; then
        KEY_NAME="id_ed25519_github"
    else
        KEY_NAME=$(ls ~/.ssh/id_*.pub | head -1 | sed 's/\.pub$//' | xargs basename)
    fi
else
    KEY_NAME="id_ed25519"
    echo "Создание нового SSH ключа..."
    ssh-keygen -t ed25519 -C "telegram-assistant@$(hostname)" -f ~/.ssh/$KEY_NAME -N ""
fi

echo ""
echo "=== Запуск SSH agent ==="
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/$KEY_NAME

echo ""
echo "=== Публичный ключ (скопируйте и добавьте на GitHub): ==="
cat ~/.ssh/${KEY_NAME}.pub

echo ""
echo "=== Откройте https://github.com/settings/keys и добавьте ключ ==="
read -p "После добавления ключа нажмите Enter для продолжения..."

echo ""
echo "=== Проверка подключения к GitHub ==="
ssh -T git@github.com

echo ""
echo "=== Переключение Git remote на SSH ==="
cd /opt/telegram-assistant
git remote set-url origin git@github.com:ilyasni/telegram-assistant.git

echo ""
echo "=== Готово! Попробуйте: git push origin <branch-name> ==="
```

---

## Troubleshooting

### Ошибка: "Permission denied (publickey)"
- Проверить, что ключ добавлен на GitHub: https://github.com/settings/keys
- Проверить подключение: `ssh -T git@github.com`
- Убедиться, что правильный ключ добавлен в ssh-agent: `ssh-add -l`

### Ошибка: "Could not resolve hostname github.com"
- Проверить интернет-соединение
- Проверить DNS: `nslookup github.com`

### Несколько SSH ключей
Если нужно использовать разные ключи для разных сервисов, настроить `~/.ssh/config`:

```bash
# ~/.ssh/config
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_github
    IdentitiesOnly yes
```

### Ключ защищен passphrase
Если забыли passphrase, нужно создать новый ключ:
```bash
ssh-keygen -t ed25519 -C "telegram-assistant@$(hostname)" -f ~/.ssh/id_ed25519_new
```

---

## Дополнительные ссылки
- GitHub Docs: https://docs.github.com/en/authentication/connecting-to-github-with-ssh
- Проверка ключей: https://github.com/settings/keys

