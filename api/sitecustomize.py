"""Interpreter bootstrap for Telegram Assistant API service."""

import sys
from pathlib import Path

_CANDIDATE_PATHS = [
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent / "worker",
    Path(__file__).resolve().parent / "telethon-ingest",
    Path(__file__).resolve().parent / "shared" / "python",
    Path("/opt/telegram-assistant/worker"),
    Path("/opt/project/worker"),
    Path("/opt/project/telethon-ingest"),
    Path("/opt/project/shared/python"),
]

_added = set()
for candidate in _CANDIDATE_PATHS:
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        continue
    if not resolved.exists():
        continue
    path_str = str(resolved)
    if path_str not in _added and path_str not in sys.path:
        sys.path.insert(0, path_str)
        _added.add(path_str)
