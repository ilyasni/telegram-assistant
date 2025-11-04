#!/bin/bash
# Entrypoint скрипт для загрузки SSL патча перед запуском gpt2giga

# Загружаем патч для отключения SSL
python3 -c "import disable_ssl" || true

# Запускаем gpt2giga с параметрами
exec gpt2giga \
    --proxy-host ${GPT2GIGA_HOST:-0.0.0.0} \
    --proxy-port ${GPT2GIGA_PROXY_PORT:-8090} \
    --proxy-verbose \
    --proxy-pass-model \
    --proxy-timeout ${GPT2GIGA_TIMEOUT:-600} \
    --proxy-embeddings ${GPT2GIGA_EMBEDDINGS:-EmbeddingsGigaR} \
    --gigachat-base-url ${GIGACHAT_BASE_URL:-https://gigachat.devices.sberbank.ru/api/v1} \
    --gigachat-verify-ssl-certs False \
    --gigachat-credentials ${GIGACHAT_CREDENTIALS} \
    --gigachat-scope ${GIGACHAT_SCOPE:-GIGACHAT_API_PERS}

