#!/usr/bin/env python3
"""Тест asyncio для worker."""

import asyncio
import sys

async def test():
    print("Async test started")
    await asyncio.sleep(0.1)
    print("Async test completed")

if __name__ == "__main__":
    print("Test 1")
    print("Test 2")
    asyncio.run(test())
    print("Test 3")
