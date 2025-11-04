"""
Патч для отключения проверки SSL в httpx/gigachat
Применяется до импорта любых модулей, использующих httpx
"""
import ssl
import urllib3
import os

# Отключаем проверку SSL на глобальном уровне
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Дополнительно устанавливаем переменные окружения
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['SSL_CERT_FILE'] = ''
os.environ['GIGACHAT_VERIFY_SSL_CERTS'] = 'False'

# Патчим httpx для отключения проверки SSL
try:
    import httpx
    # Модифицируем create_default_context для httpx
    original_create_context = ssl.create_default_context
    def unverified_context(*args, **kwargs):
        ctx = original_create_context(*args, **kwargs)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ssl.create_default_context = unverified_context
    
    # Дополнительно патчим httpx Client для использования verify=False по умолчанию
    original_init = httpx.Client.__init__
    def patched_init(self, *args, verify=False, **kwargs):
        return original_init(self, *args, verify=verify, **kwargs)
    httpx.Client.__init__ = patched_init
    
    original_async_init = httpx.AsyncClient.__init__
    def patched_async_init(self, *args, verify=False, **kwargs):
        return original_async_init(self, *args, verify=verify, **kwargs)
    httpx.AsyncClient.__init__ = patched_async_init
except ImportError:
    pass

