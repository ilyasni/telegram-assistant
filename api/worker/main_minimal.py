#!/usr/bin/env python3
"""Минимальный main для диагностики."""

print("=== MINIMAL MAIN: TOP OF FILE ===", flush=True)

import time

print("=== MINIMAL MAIN: TIME IMPORTED ===", flush=True)

for i in range(10):
    print(f"=== MINIMAL MAIN: Loop {i} ===", flush=True)
    time.sleep(1)

print("=== MINIMAL MAIN: FINISHED ===", flush=True)
