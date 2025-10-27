#!/usr/bin/env python3
"""
Прямой тест подключения к GigaChat API через requests
"""
import os
import requests
import urllib3

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_direct_gigachat():
    """Прямой тест GigaChat API"""
    try:
        print("Testing direct GigaChat API connection...")
        
        # Получаем токен доступа
        credentials = os.getenv('GIGACHAT_CREDENTIALS')
        scope = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')
        
        print(f"Credentials: {credentials[:20]}...")
        print(f"Scope: {scope}")
        
        # Запрос токена
        auth_url = "https://gigachat.devices.sberbank.ru/api/v2/oauth"
        auth_data = {
            "scope": scope
        }
        auth_headers = {
            "Authorization": f"Bearer {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        print("Requesting access token...")
        response = requests.post(
            auth_url, 
            data=auth_data, 
            headers=auth_headers,
            verify=False,  # Отключаем проверку SSL
            timeout=30
        )
        
        print(f"Auth response status: {response.status_code}")
        print(f"Auth response: {response.text[:200]}...")
        
        if response.status_code == 200:
            print("✅ Direct GigaChat API connection successful!")
            return True
        else:
            print(f"❌ Auth failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Direct GigaChat API connection failed: {e}")
        print(f"Error type: {type(e).__name__}")
        return False

if __name__ == "__main__":
    test_direct_gigachat()
