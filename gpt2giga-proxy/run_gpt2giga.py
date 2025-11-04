#!/usr/bin/env python3
"""
Wrapper для запуска gpt2giga с применением SSL патча
"""
import sys
import os

# Применяем патч ДО импорта любых модулей
import disable_ssl

# Теперь можем импортировать и запускать gpt2giga
import subprocess

# Получаем аргументы из переменных окружения
args = [
    'gpt2giga',
    '--proxy-host', os.getenv('GPT2GIGA_HOST', '0.0.0.0'),
    '--proxy-port', os.getenv('GPT2GIGA_PROXY_PORT', '8090'),
    '--proxy-verbose',
    '--proxy-pass-model',
    '--proxy-timeout', os.getenv('GPT2GIGA_TIMEOUT', '600'),
    '--proxy-embeddings', os.getenv('GPT2GIGA_EMBEDDINGS', 'EmbeddingsGigaR'),
    '--gigachat-base-url', os.getenv('GIGACHAT_BASE_URL', 'https://gigachat.devices.sberbank.ru/api/v1'),
    '--gigachat-verify-ssl-certs', 'False',
]

if os.getenv('GIGACHAT_CREDENTIALS'):
    args.extend(['--gigachat-credentials', os.getenv('GIGACHAT_CREDENTIALS')])

if os.getenv('GIGACHAT_SCOPE'):
    args.extend(['--gigachat-scope', os.getenv('GIGACHAT_SCOPE')])

# Подготавливаем окружение для subprocess с SSL отключенным
env = os.environ.copy()
env['PYTHONHTTPSVERIFY'] = '0'
env['CURL_CA_BUNDLE'] = ''
env['REQUESTS_CA_BUNDLE'] = ''
env['SSL_CERT_FILE'] = ''
env['GIGACHAT_VERIFY_SSL_CERTS'] = 'False'  # Строка "False" для переменной окружения

# Запускаем gpt2giga с подготовленным окружением
sys.exit(subprocess.run(args, env=env).returncode)

