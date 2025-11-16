from datetime import datetime
from typing import Any, Dict, List


def serialize_example_posts(posts: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for post in posts[:limit]:
        posted_at = post.get("posted_at")
        if isinstance(posted_at, datetime):
            posted_at_value = posted_at.isoformat()
            if posted_at_value.endswith("+00:00"):
                posted_at_value = posted_at_value.replace("+00:00", "Z")
        else:
            posted_at_value = posted_at
        serialized.append(
            {
                "post_id": post.get("post_id"),
                "channel_id": post.get("channel_id"),
                "channel_title": post.get("channel_title"),
                "posted_at": posted_at_value,
                "content_snippet": post.get("content_snippet"),
            }
        )
    return serialized


def fallback_summary_from_posts(posts: List[Dict[str, Any]], limit: int = 2) -> str:
    snippets: List[str] = []
    for post in posts:
        snippet = (post.get("content_snippet") or "").strip()
        if not snippet:
            continue
        sanitized = snippet.replace("\n", " ")
        if sanitized and sanitized not in snippets:
            snippets.append(sanitized)
        if len(snippets) >= limit:
            break
    return " ".join(snippets)[:600]


def fallback_why_from_stats(stats: Dict[str, Any]) -> str:
    mentions = stats.get("mentions") or 0
    baseline = stats.get("baseline") or 1
    ratio = stats.get("burst_score") or (mentions / max(1, baseline))
    window_minutes = stats.get("window_minutes") or 60
    return (
        f"За последние {window_minutes} мин зафиксировано {mentions} упоминаний — "
        f"примерно в {ratio:.1f}× чаще, чем обычные {baseline} за период."
    )

