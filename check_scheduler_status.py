#!/usr/bin/env python3
"""Проверка состояния scheduler в telethon-ingest."""
import requests
import json
import sys

try:
    r = requests.get('http://localhost:8011/health/details', timeout=5)
    if r.status_code == 200:
        data = r.json()
        scheduler = data.get('scheduler', {})
        parser = data.get('parser', {})
        
        print("=" * 60)
        print("SCHEDULER STATUS")
        print("=" * 60)
        print(f"Status: {scheduler.get('status', 'unknown')}")
        print(f"Last tick: {scheduler.get('last_tick_ts', 'never')}")
        print(f"Interval: {scheduler.get('interval_sec', 'unknown')}s")
        print(f"Lock owner: {scheduler.get('lock_owner', 'none')}")
        
        print("\n" + "=" * 60)
        print("PARSER STATUS")
        print("=" * 60)
        print(f"Initialized: {parser.get('initialized', False)}")
        print(f"Version: {parser.get('version', 'unknown')}")
        
        print("\n" + "=" * 60)
        print("OVERALL STATUS")
        print("=" * 60)
        print(f"App status: {data.get('status', 'unknown')}")
        print(f"Phase: {data.get('phase', 'unknown')}")
        
        # Проверка критичности
        if scheduler.get('status') == 'down':
            print("\n❌ CRITICAL: Scheduler is DOWN!")
            sys.exit(1)
        elif scheduler.get('status') == 'stale':
            print("\n⚠️  WARNING: Scheduler is STALE!")
            sys.exit(2)
        else:
            print("\n✅ Scheduler appears to be running")
            sys.exit(0)
    else:
        print(f"❌ Health endpoint returned {r.status_code}")
        sys.exit(1)
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to health endpoint (http://localhost:8011/health/details)")
    print("   Is telethon-ingest container running?")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)

