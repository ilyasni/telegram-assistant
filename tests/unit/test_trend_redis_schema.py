from shared.trends import TrendRedisSchema, TrendWindow, TRENDS_EMERGING_STREAM


def test_freq_key_format():
    schema = TrendRedisSchema()
    assert schema.freq_key("abc123", TrendWindow.SHORT_5M) == "trend:abc123:freq:5m"
    assert schema.freq_key("cluster", TrendWindow.MID_1H) == "trend:cluster:freq:1h"


def test_window_seconds_mapping():
    assert TrendWindow.SHORT_5M.seconds == 300
    assert TrendWindow.MID_1H.seconds == 3600
    assert TrendWindow.LONG_24H.seconds == 86400


def test_source_and_stream_helpers():
    schema = TrendRedisSchema()
    cluster_id = "foo"
    assert schema.source_set_key(cluster_id) == "trend:foo:sources"
    assert schema.burst_key(cluster_id) == "trend:foo:burst"
    assert schema.emerging_stream() == TRENDS_EMERGING_STREAM


