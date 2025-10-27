#!/usr/bin/env python3
"""
Запуск tagging_task с правильными путями.
"""

import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем и запускаем tagging_task
if __name__ == "__main__":
    import asyncio
    from tasks.tagging_task import main
    asyncio.run(main())
