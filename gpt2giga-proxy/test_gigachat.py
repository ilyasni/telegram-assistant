#!/usr/bin/env python3
"""
Тест подключения к GigaChat API
"""
import os
import ssl
import urllib3
from gigachat import GigaChat

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Отключаем проверку SSL на уровне окружения
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''

def test_gigachat():
    """Тест подключения к GigaChat"""
    try:
        print("Testing GigaChat connection...")
        print(f"Credentials: {os.getenv('GIGACHAT_CREDENTIALS')[:20]}...")
        print(f"Scope: {os.getenv('GIGACHAT_SCOPE')}")
        
        # Создаем клиент с отключенной проверкой SSL для тестирования
        client = GigaChat(
            credentials=os.getenv('GIGACHAT_CREDENTIALS'),
            scope=os.getenv('GIGACHAT_SCOPE'),
            verify_ssl_certs=False
        )
        
        print("Client created successfully")
        
        # Тестовый запрос
        response = client.chat("Привет! Это тест.")
        print(f"Response: {response.choices[0].message.content}")
        print("✅ GigaChat connection successful!")
        
    except Exception as e:
        print(f"❌ GigaChat connection failed: {e}")
        print(f"Error type: {type(e).__name__}")

if __name__ == "__main__":
    test_gigachat()
