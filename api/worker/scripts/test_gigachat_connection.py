#!/usr/bin/env python3
"""
Простой тест подключения к GigaChat прокси.
"""
import socket
import sys

host = 'gpt2giga-proxy'
port = 8090

print(f"Проверка подключения к {host}:{port}...")

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    result = sock.connect_ex((host, port))
    sock.close()
    
    if result == 0:
        print(f"✅ Порт {port} доступен!")
        sys.exit(0)
    else:
        print(f"❌ Порт {port} недоступен (код ошибки: {result})")
        sys.exit(1)
except Exception as e:
    print(f"❌ Ошибка: {e}")
    sys.exit(1)

