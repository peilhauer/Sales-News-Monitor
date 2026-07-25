"""
Thin status wrapper around the news connector's on-disk cache
(connectors/news.py owns the actual read/write — this just reports on it
for the /cache/status endpoint, mirroring GRI Screener's monitoring/cache.py
shape without duplicating the caching logic itself).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

NEWS_CACHE_FILE = Path("data/cache/news_cache.json")


def cache_status() -> dict:
    if not NEWS_CACHE_FILE.exists():
        return {"status": "empty", "entry_count": 0}
    try:
        data = json.loads(NEWS_CACHE_FILE.read_text())
        ages = []
        for entry in data.values():
            try:
                cached_at = datetime.fromisoformat(entry["cached_at"])
                ages.append((datetime.now(timezone.utc) - cached_at).total_seconds() / 3600)
            except Exception:
                continue
        return {
            "status": "populated",
            "entry_count": len(data),
            "newest_entry_age_hours": round(min(ages), 2) if ages else None,
            "oldest_entry_age_hours": round(max(ages), 2) if ages else None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
