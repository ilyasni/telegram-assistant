#!/usr/bin/env python3
"""
Wrapper для запуска gpt2giga с применением SSL патча
Использует прямое применение патча httpx вместо subprocess
"""
import sys
import os

# Применяем патч ДО импорта любых модулей
import disable_ssl

# Теперь импортируем gpt2giga - патч уже применен
try:
    # Попытка импортировать и запустить gpt2giga напрямую
    from gpt2giga import main as gpt2giga_main
    
    # Устанавливаем аргументы как sys.argv
    original_argv = sys.argv
    sys.argv = [
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
        sys.argv.extend(['--gigachat-credentials', os.getenv('GIGACHAT_CREDENTIALS')])
    
    if os.getenv('GIGACHAT_SCOPE'):
        sys.argv.extend(['--gigachat-scope', os.getenv('GIGACHAT_SCOPE')])
    
    # Запускаем gpt2giga
    gpt2giga_main()
    
except ImportError:
    # Если не можем импортировать, используем subprocess, но с патчем в окружении
    import subprocess
    
    args = [
        'python3', '-c',
        'import disable_ssl; import subprocess; import sys; sys.argv = []; '
        'from gpt2giga import main; main()'
    ]
    
    # Подготавливаем окружение
    env = os.environ.copy()
    env['PYTHONHTTPSVERIFY'] = '0'
    env['CURL_CA_BUNDLE'] = ''
    env['REQUESTS_CA_BUNDLE'] = ''
    env['SSL_CERT_FILE'] = ''
    env['GIGACHAT_VERIFY_SSL_CERTS'] = 'False'
    
    # Устанавливаем переменные для subprocess
    for key, value in [
        ('GPT2GIGA_HOST', os.getenv('GPT2GIGA_HOST', '0.0.0.0')),
        ('GPT2GIGA_PROXY_PORT', os.getenv('GPT2GIGA_PROXY_PORT', '8090')),
        ('GPT2GIGA_TIMEOUT', os.getenv('GPT2GIGA_TIMEOUT', '600')),
        ('GPT2GIGA_EMBEDDINGS', os.getenv('GPT2GIGA_EMBEDDINGS', 'EmbeddingsGigaR')),
        ('GIGACHAT_BASE_URL', os.getenv('GIGACHAT_BASE_URL', 'https://gigachat.devices.sberbank.ru/api/v1')),
    ]:
        env[key] = value
    
    sys.exit(subprocess.run(['gpt2giga'] + [
        '--proxy-host', env['GPT2GIGA_HOST'],
        '--proxy-port', env['GPT2GIGA_PROXY_PORT'],
        '--proxy-verbose',
        '--proxy-pass-model',
        '--proxy-timeout', env['GPT2GIGA_TIMEOUT'],
        '--proxy-embeddings', env['GPT2GIGA_EMBEDDINGS'],
        '--gigachat-base-url', env['GIGACHAT_BASE_URL'],
        '--gigachat-verify-ssl-certs', 'False',
    ] + (['--gigachat-credentials', os.getenv('GIGACHAT_CREDENTIALS')] if os.getenv('GIGACHAT_CREDENTIALS') else []) +
        (['--gigachat-scope', os.getenv('GIGACHAT_SCOPE')] if os.getenv('GIGACHAT_SCOPE') else []),
        env=env).returncode)

