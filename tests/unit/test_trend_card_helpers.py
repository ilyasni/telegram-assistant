from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.trends import card_utils


def test_serialize_example_posts_serializes_datetimes():
    posts = [
        {
            "post_id": "123",
            "channel_id": "channel",
            "channel_title": "Source",
            "posted_at": datetime(2025, 11, 15, 12, 30, tzinfo=timezone.utc),
            "content_snippet": "Example text",
        }
    ]

    serialized = card_utils.serialize_example_posts(posts)

    assert serialized[0]["posted_at"].endswith("Z")
    assert serialized[0]["content_snippet"] == "Example text"

