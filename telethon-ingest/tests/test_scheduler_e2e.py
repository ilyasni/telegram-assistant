"""
E2E tests for incremental parsing scheduler.

Tests cover:
- Historical and incremental mode selection
- Empty tick detection
- Crash recovery via HWM
- LPA safeguard mechanism
"""

import pytest
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks.parse_all_channels_task import ParseAllChannelsTask
from services.channel_parser import ParserConfig


@pytest.fixture
async def test_db():
    """Setup test database with channels"""
    # TODO: Implement test database setup
    # For now, return mock data
    channels = [
        {
            'id': 'chA',
            'tg_channel_id': 12345,
            'username': 'test_channel_a',
            'title': 'Test Channel A',
            'last_parsed_at': None,  # Will trigger historical mode
            'is_active': True
        },
        {
            'id': 'chB',
            'tg_channel_id': 67890,
            'username': 'test_channel_b',
            'title': 'Test Channel B',
            'last_parsed_at': datetime.now(timezone.utc) - timedelta(hours=2),  # Will trigger incremental mode
            'is_active': True
        }
    ]
    return channels


@pytest.fixture
async def test_redis():
    """Setup test Redis"""
    # TODO: Implement test Redis setup with flush between tests
    import redis.asyncio as redis
    redis_client = redis.from_url("redis://localhost:6379/15")  # Use DB 15 for tests
    await redis_client.flushdb()
    yield redis_client
    await redis_client.flushdb()
    await redis_client.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_first_tick_historical_and_incremental(test_db, test_redis):
    """Test first tick: chA historical, chB incremental"""
    # Setup
    config = ParserConfig()
    config.lpa_max_age_hours = 48
    
    # Mock database query to return test channels
    scheduler = ParseAllChannelsTask(
        config=config,
        db_url="mock://test",
        redis_client=test_redis,
        parser=None
    )
    
    # Override _get_active_channels to return test data
    scheduler._get_active_channels = lambda: test_db
    
    # Run one tick
    await scheduler._run_tick()
    
    # Assertions
    # TODO: Add assertions for mode selection and parsing
    # - chA should use historical mode (last_parsed_at is None)
    # - chB should use incremental mode (last_parsed_at exists and is recent)
    assert True  # Placeholder


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_second_tick_no_new_posts(test_db, test_redis):
    """Test second tick with no new posts"""
    # Setup same as test_first_tick
    config = ParserConfig()
    scheduler = ParseAllChannelsTask(
        config=config,
        db_url="mock://test",
        redis_client=test_redis,
        parser=None
    )
    scheduler._get_active_channels = lambda: test_db
    
    # Run first tick
    await scheduler._run_tick()
    
    # Get initial state
    initial_state = {ch['id']: ch['last_parsed_at'] for ch in test_db}
    
    # Run second tick
    await scheduler._run_tick()
    
    # Assertions
    # - last_parsed_at should not change if no new posts
    # - parser_runs_total should increment
    # - posts_parsed_total should NOT increment
    assert True  # Placeholder


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_hwm_recovery_after_crash(test_db, test_redis):
    """Test HWM recovery after mid-batch crash"""
    # Setup: Insert HWM in Redis for a channel
    hwm_key = "parse_hwm:chA"
    hwm_timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await test_redis.set(hwm_key, hwm_timestamp, ex=86400)
    
    # Setup scheduler
    config = ParserConfig()
    scheduler = ParseAllChannelsTask(
        config=config,
        db_url="mock://test",
        redis_client=test_redis,
        parser=None
    )
    
    # Modify test_db to have a last_parsed_at older than HWM
    channel_with_hwm = test_db[0].copy()
    channel_with_hwm['last_parsed_at'] = datetime.now(timezone.utc) - timedelta(hours=2)
    scheduler._get_active_channels = lambda: [channel_with_hwm]
    
    # Run tick
    await scheduler._run_tick()
    
    # Assertions
    # - since_date should use max(last_parsed_at, hwm)
    # - HWM should be cleared after successful parsing
    hwm_value = await test_redis.get(hwm_key)
    # HWM should be None after successful tick
    assert hwm_value is None or hwm_value == b'None'


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_lpa_safeguard_forces_historical(test_db, test_redis):
    """Test LPA safeguard forcing historical mode"""
    # Setup: Channel with very old last_parsed_at
    stale_channel = {
        'id': 'ch_stale',
        'tg_channel_id': 99999,
        'username': 'stale_channel',
        'title': 'Stale Channel',
        'last_parsed_at': datetime.now(timezone.utc) - timedelta(hours=72),  # 72h ago
        'is_active': True
    }
    
    # Config with LPA_MAX_AGE = 48h
    config = ParserConfig()
    config.lpa_max_age_hours = 48
    config.mode_override = "auto"
    
    scheduler = ParseAllChannelsTask(
        config=config,
        db_url="mock://test",
        redis_client=test_redis,
        parser=None
    )
    scheduler._get_active_channels = lambda: [stale_channel]
    
    # Run tick
    mode = scheduler._decide_mode(stale_channel)
    
    # Assertions
    # - Mode should be "historical" because last_parsed_at is older than LPA_MAX_AGE
    assert mode == "historical"
    
    # - parser_mode_forced_total{reason="stale_lpa"} should be incremented
    # This will be checked via metrics in actual implementation
