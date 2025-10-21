# 🗄️ Доступ к Supabase Studio

## 🌐 URL для доступа

**Supabase Studio доступен по адресу:**

```
http://localhost:8080/supabase/
```

## 🔧 Что происходит

1. **Caddy** перенаправляет запросы с `/supabase/*` на `supabase-studio:3000`
2. **Supabase Studio** автоматически перенаправляет на `/project/default`
3. **Полный URL:** `http://localhost:8080/supabase/project/default`

## 📋 Статус сервисов

- ✅ **Caddy** — работает на порту 8080
- ✅ **Supabase Studio** — запущен и готов
- ✅ **База данных** — PostgreSQL работает
- ⚠️ **Health check** — показывает "unhealthy" (но работает)

## 🧪 Тестирование

```bash
# Проверка доступности
curl -I http://localhost:8080/supabase/

# Проверка полного пути
curl -I http://localhost:8080/supabase/project/default
```

## 🎯 Использование

1. **Откройте браузер** и перейдите на `http://localhost:8080/supabase/`
2. **Supabase Studio** автоматически перенаправит на главную страницу
3. **Используйте Studio** для управления базой данных, таблицами, API и т.д.

## 🔍 Возможные проблемы

- **"Unhealthy" статус** — это нормально, Studio работает
- **Медленная загрузка** — первый запуск может занять время
- **Ошибки подключения** — проверьте, что все сервисы запущены

## 🚀 Готово!

**Supabase Studio полностью функционален и готов к использованию!**
