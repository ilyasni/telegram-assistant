"""
Глобальные фикстуры и настройка путей для pytest.

Context7: Добавляем корневые модули api/worker/telethon-ingest в sys.path,
чтобы тесты могли импортировать сервисы без дополнительных настроек окружения.
"""

import os
import sys
import types
from pathlib import Path

import pytest


def _extend_sys_path() -> None:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "api",
        root / "worker",
        root / "telethon-ingest",
        root / "shared" / "python",
        root / "crawl4ai",
    ]

    telethon_ingest_path = None
    shared_package_root = None

    for path in candidates:
        if path.exists():
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            if path.name == "telethon-ingest":
                telethon_ingest_path = path
            if path.name == "python":
                potential_shared = path / "shared"
                if potential_shared.exists():
                    shared_package_root = potential_shared

    if telethon_ingest_path and "telethon_ingest" not in sys.modules:
        import importlib.machinery

        module_name = "telethon_ingest"
        spec = importlib.machinery.ModuleSpec(module_name, loader=None, is_package=True)
        module = types.ModuleType(module_name)
        module.__spec__ = spec
        module.__path__ = [str(telethon_ingest_path)]
        sys.modules[module_name] = module

    if shared_package_root and "shared" not in sys.modules:
        import importlib.machinery

        module_name = "shared"
        spec = importlib.machinery.ModuleSpec(module_name, loader=None, is_package=True)
        module = types.ModuleType(module_name)
        module.__spec__ = spec
        module.__path__ = [str(shared_package_root)]
        sys.modules[module_name] = module

    if "asyncpg" not in sys.modules:
        asyncpg_stub = types.ModuleType("asyncpg")

        class _Pool:
            async def acquire(self):  # pragma: no cover - заглушка
                raise NotImplementedError

        asyncpg_stub.Pool = _Pool
        sys.modules["asyncpg"] = asyncpg_stub


_extend_sys_path()


@pytest.fixture
def db_dsn():
    dsn = os.getenv("TEST_DB_DSN")
    if not dsn:
        pytest.skip("TEST_DB_DSN не задан, пропуск интеграционных тестов с реальной БД")
    return dsn


@pytest.fixture
def file_path():
    pytest.skip("Нет входного файла для vision-интеграции; тест пропущен по умолчанию")

