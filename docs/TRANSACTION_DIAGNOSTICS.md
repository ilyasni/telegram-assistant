# Диагностика транзакций и проблем с сохранением данных

**Дата**: 2025-11-05  
**Проблема**: В БД нет новых данных, несмотря на работу парсера

## Обнаруженные проблемы

### 1. Недостаточное логирование
- **Проблема**: Нет детальных логов о выполнении транзакций
- **Решение**: Добавлено детальное логирование на каждом этапе транзакции

### 2. User не найден при создании user_channel
- **Проблема**: `_ensure_user_channel` может не найти user, если он не был создан в транзакции
- **Решение**: 
  - Добавлен `flush()` перед проверкой user
  - Добавлена верификация user после upsert
  - Улучшено логирование

### 3. Транзакция может откатываться без явных ошибок
- **Проблема**: Если `_ensure_user_channel` бросает исключение, транзакция откатывается
- **Решение**: Обернули `_ensure_user_channel` в try-except, чтобы не прерывать транзакцию

## Добавленное логирование

### В `save_batch_atomic`:
1. `Starting atomic batch save transaction` - начало транзакции
2. `Upserting user` - начало upsert user
3. `Membership upserted successfully` - успешный upsert user
4. `User verified in database` - верификация user в БД
5. `Upserting channel` - начало upsert channel
6. `Channel upserted successfully` - успешный upsert channel
7. `_ensure_user_channel called` - вызов создания user_channel
8. `user_channel created successfully` - успешное создание user_channel
9. `Bulk inserting posts` - начало bulk insert постов
10. `Posts bulk inserted` - успешный bulk insert
11. `Atomic batch save successful` - успешное завершение транзакции

### В `_ensure_user_channel`:
1. `_ensure_user_channel called` - начало метода
2. `User not found for user_channel creation` - user не найден (WARNING)
3. `user_channel already exists` - связь уже существует (DEBUG)
4. `user_channel created successfully` - успешное создание (INFO)
5. `Failed to ensure user_channel` - ошибка (ERROR с полным traceback)

## Context7 Best Practices

### 1. Детальное логирование
- ✅ Логирование на каждом этапе транзакции
- ✅ Логирование ошибок с полным traceback
- ✅ Логирование успешных операций для диагностики

### 2. Защита транзакций
- ✅ Не критичные операции обёрнуты в try-except
- ✅ Использование `flush()` для видимости изменений в транзакции
- ✅ Верификация данных после операций

### 3. Обработка ошибок
- ✅ Детальное логирование ошибок с типом исключения
- ✅ Не прерываем транзакцию при некритичных ошибках
- ✅ Метрики для мониторинга ошибок

## Возможные проблемы

### 1. User не создаётся
- **Причина**: Ошибка в `upsert_identity_and_membership_async`
- **Диагностика**: Проверить логи на "Membership upserted successfully"
- **Решение**: Исправить ошибку в upsert user

### 2. Транзакция откатывается
- **Причина**: Ошибка в критичных операциях (upsert user/channel, bulk insert)
- **Диагностика**: Проверить логи на "Atomic batch save failed"
- **Решение**: Исправить ошибку в соответствующей операции

### 3. user_channel не создаётся
- **Причина**: User не найден или ошибка при вставке
- **Диагностика**: Проверить логи на "Failed to ensure user_channel"
- **Решение**: Исправить проблему с user или FK ограничениями

### 4. Альбомы не сохраняются
- **Причина**: Ошибки в `save_media_group` или отсутствие user_channel
- **Диагностика**: Проверить логи на "Failed to upsert media_group"
- **Решение**: Исправить ошибки в save_media_group или создать user_channel

## Следующие шаги

1. Проверить логи после перезапуска на наличие новых сообщений
2. Проверить, вызывается ли `save_batch_atomic` вообще
3. Проверить, есть ли ошибки при создании user/channel
4. Проверить, создаётся ли user_channel

