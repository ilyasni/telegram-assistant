# SearXNG - Исправление прав доступа и удаление limiter.toml

**Дата**: 2025-02-02

## 🔧 Команды для исправления прав доступа и удаления limiter.toml

### Проблема
1. Ошибка при сохранении `settings.yml`: `EACCES: permission denied`
2. SearXNG падает с ошибкой: `TypeError: schema of /etc/searxng/limiter.toml is invalid!`

### Решение

#### Шаг 1: Удалить limiter.toml (обязательно!)
```bash
sudo rm -f /opt/telegram-assistant/searxng/limiter.toml
```

**Важно**: Файл `limiter.toml` принадлежит root (uid 977), поэтому нужен sudo.

#### Шаг 2: Изменить владельца settings.yml (если еще не сделано)
```bash
sudo chown $USER:$USER /opt/telegram-assistant/searxng/settings.yml
```

#### Шаг 3: Перезапустить SearXNG
```bash
docker compose --profile rag up -d searxng
```

### Почему это работает

1. **Context7 Best Practices**: Согласно документации SearXNG, для отключения limiter достаточно установить `limiter: false` в `settings.yml` (строка 90).
2. **limiter.toml**: Если файл существует, SearXNG пытается его загрузить и валидировать. Если он пустой или имеет неправильную схему - возникает ошибка. Поэтому файл нужно удалить полностью.
3. **settings.yml**: Уже настроен с `limiter: false` и `public_instance: false` (строки 90, 94).

### Проверка
```bash
# Проверить, что limiter.toml удален
ls -la /opt/telegram-assistant/searxng/limiter.toml
# Должно показать: ls: cannot access 'limiter.toml': No such file or directory

# Проверить владельца settings.yml
ls -la /opt/telegram-assistant/searxng/settings.yml
# Должно показать вашего пользователя как владельца

# Проверить статус SearXNG
docker compose ps searxng
```

### После исправления

SearXNG должен запуститься без ошибок, и bot detection будет отключен через `settings.yml`:
- `limiter: false` (строка 90)
- `public_instance: false` (строка 94)
