#!/usr/bin/env python3
"""
Тест credentials для GigaChat API
"""

import base64
import requests
import json
import ssl
import urllib3

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_credentials():
    """Тестирует credentials для GigaChat API"""
    
    # Credentials из .env
    credentials = "your_gigachat_credentials_base64_here"
    
    print("🔍 Тестирование GigaChat credentials...")
    print(f"Credentials: {credentials[:20]}...")
    
    # Декодируем credentials
    try:
        decoded = base64.b64decode(credentials).decode('utf-8')
        print(f"Декодированные credentials: {decoded}")
        
        if ':' in decoded:
            client_id, client_secret = decoded.split(':', 1)
            print(f"Client ID: {client_id}")
            print(f"Client Secret: {client_secret[:10]}...")
        else:
            print("❌ Неверный формат credentials")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка декодирования: {e}")
        return False
    
    # Тестируем подключение к GigaChat API
    print("\n🌐 Тестирование подключения к GigaChat API...")
    
    try:
        # URL для авторизации
        auth_url = "https://gigachat.devices.sberbank.ru/api/v1/oauth"
        
        # Заголовки
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Данные для запроса
        data = {
            "scope": "GIGACHAT_API_PERS"
        }
        
        print(f"URL: {auth_url}")
        print(f"Headers: {headers}")
        print(f"Data: {data}")
        
        # Отправляем запрос с отключенной проверкой SSL
        response = requests.post(auth_url, data=data, headers=headers, timeout=30, verify=False)
        
        print(f"\n📊 Результат запроса:")
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Response: {response.text[:500]}...")
        
        if response.status_code == 200:
            print("✅ Авторизация успешна!")
            try:
                token_data = response.json()
                print(f"Access Token: {token_data.get('access_token', 'N/A')[:20]}...")
                return True
            except:
                print("⚠️ Ответ не в формате JSON")
                return False
        else:
            print(f"❌ Ошибка авторизации: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False

if __name__ == "__main__":
    test_credentials()
