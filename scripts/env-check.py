#!/usr/bin/env python3
"""Валидация .env файла через Python jsonschema (замена ajv-cli).

[C7-ID: ENV-SEC-003] Валидация локального .env по схеме
Context7 best practice: использовать Python инструменты в Python проекте.
"""
import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional


def parse_env_file(env_path: Path) -> Dict[str, str]:
    """Парсинг .env файла в словарь."""
    result = {}
    if not env_path.exists():
        raise FileNotFoundError(f".env файл не найден: {env_path}")
    
    with open(env_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # Пропускаем пустые строки и комментарии
            if not line or line.startswith("#"):
                continue
            
            # Разделяем на ключ и значение (только первое =)
            if "=" not in line:
                print(f"WARNING: строка {line_num} не содержит '=': {line}", file=sys.stderr)
                continue
            
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            
            # Убираем кавычки если есть
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            
            result[key] = value
    
    return result


def validate_with_schema(env_dict: Dict[str, str], schema_path: Optional[Path] = None) -> Optional[bool]:
    """Валидация через JSON Schema (опционально).
    
    Returns:
        True: валидация пройдена
        False: ошибка валидации
        None: jsonschema не установлен (предупреждение, не ошибка)
    """
    if schema_path and schema_path.exists():
        try:
            from jsonschema import validate, Draft202012Validator, ValidationError
            
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            
            # Валидация схемы
            Draft202012Validator.check_schema(schema)
            
            # Валидация данных
            validate(instance=env_dict, schema=schema)
            return True
        except ImportError:
            print("WARNING: jsonschema не установлен, пропускаем валидацию", file=sys.stderr)
            print("  Установите: pip install jsonschema", file=sys.stderr)
            return None  # None = предупреждение, не ошибка
        except ValidationError as e:
            print(f"ERROR: Валидация .env не пройдена:", file=sys.stderr)
            print(f"  {e.message}", file=sys.stderr)
            if e.path:
                print(f"  Путь: {list(e.path)}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"ERROR: Ошибка валидации схемы: {e}", file=sys.stderr)
            return False
    else:
        # Схема не найдена - пропускаем валидацию
        return True


def main():
    """Главная функция."""
    repo_root = Path(__file__).parent.parent
    env_path = repo_root / ".env"
    schema_path = repo_root / ".env.schema.json"
    
    # Парсинг .env
    try:
        env_dict = parse_env_file(env_path)
        print(f"INFO: Загружено {len(env_dict)} переменных из .env")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Ошибка парсинга .env: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Сохранение .env.json для совместимости с CI
    env_json_path = repo_root / ".env.json"
    try:
        with open(env_json_path, "w", encoding="utf-8") as f:
            json.dump(env_dict, f, indent=2, ensure_ascii=False)
        print(f"INFO: .env.json создан")
    except Exception as e:
        print(f"WARNING: Не удалось создать .env.json: {e}", file=sys.stderr)
    
    # Валидация через схему (если есть)
    validation_passed = True
    if schema_path.exists():
        validation_result = validate_with_schema(env_dict, schema_path)
        if validation_result is False:
            # Ошибка валидации - выход с ошибкой
            sys.exit(1)
        elif validation_result is True:
            print("INFO: Валидация по схеме пройдена")
            validation_passed = True
        elif validation_result is None:
            # jsonschema не установлен, но схема есть - предупреждение уже выведено
            validation_passed = True
    else:
        print("INFO: Схема .env.schema.json не найдена, валидация пропущена")
    
    if validation_passed:
        print("env OK")
    sys.exit(0)


if __name__ == "__main__":
    main()

