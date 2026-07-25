"""
News search connectors for the WARN layoff monitor.

Ported from the GRI Screener's connectors/news.py, adapted to search for
company-level layoff/restructuring signals instead of a person's name.
Two providers, either or both may be enabled per config.py:

  - GDELT: free, no API key. Returns article metadata only (title/url/
    date/domain) — no full text, so fetch_article_text() must be used
    separately before any relevance judgment.
  - NewsAPI.org: requires NEWSAPI_KEY in the environment. Returns a
    description/content snippet in addition to metadata. If NEWSAPI_KEY
    isn't set, this connector logs a warning and returns no results
    rather than failing the run.

A missing or failed source is reported as an explicit error string, never
silently folded into an empty result — a real "nothing found" and a broken
connector must never look identical on the dashboard.
"""

import os
import re
import json
import html
import time
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"

HEADERS = {
    "User-Agent": "WARN-Layoff-Monitor/1.0 (sales research)",
}

# Layoff-relevant keywords appended to every company search so results skew
# toward workforce events rather than every mention of the company.
LAYOFF_QUERY_TERMS = '(layoffs OR "WARN Act" OR restructuring OR "job cuts" OR headcount)'

GDELT_MIN_INTERVAL_SECONDS = 5.0
NEWS_CACHE_TTL_HOURS = 6
NEWS_CACHE_DIR = Path("data/cache")
NEWS_CACHE_FILE = NEWS_CACHE_DIR / "news_cache.json"

_gdelt_lock = threading.Lock()
_gdelt_last_call = 0.0
_cache_file_lock = threading.Lock()


def _throttle_gdelt():
    """Keep GDELT requests at least GDELT_MIN_INTERVAL_SECONDS apart, process-wide."""
    global _gdelt_last_call
    with _gdelt_lock:
        wait = GDELT_MIN_INTERVAL_SECONDS - (time.monotonic() - _gdelt_last_call)
        if wait > 0:
            time.sleep(wait)
        _gdelt_last_call = time.monotonic()


def _cache_key(provider: str, company: str, max_records: int) -> str:
    return f"{provider}:{company.strip().lower()}:{max_records}"


def _load_news_cache() -> dict:
    if not NEWS_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(NEWS_CACHE_FILE.read_text())
    except Exception as e:
        logger.warning(f"News cache read failed, treating as empty: {e}")
        return {}


def _save_news_cache(cache: dict):
    try:
        NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        NEWS_CACHE_FILE.write_text(json.dumps(cache, default=str))
    except Exception as e:
        logger.warning(f"News cache write failed: {e}")


def _get_cached_articles(provider: str, company: str, max_records: int) -> Optional[List[Dict]]:
    with _cache_file_lock:
        entry = _load_news_cache().get(_cache_key(provider, company, max_records))
    if not entry:
        return None
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
    except Exception:
        return None
    if datetime.now(timezone.utc) - cached_at > timedelta(hours=NEWS_CACHE_TTL_HOURS):
        return None
    return entry["articles"]


def _set_cached_articles(provider: str, company: str, max_records: int, articles: List[Dict]):
    with _cache_file_lock:
        cache = _load_news_cache()
        cache[_cache_key(provider, company, max_records)] = {
            "articles":  articles,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_news_cache(cache)


def _get_with_retry(url: str, params: dict, timeout: int, retries: int = 1, backoff: float = 2.0):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                hint = f" (server suggests retrying after {retry_after}s)" if retry_after else ""
                e.args = (f"{e}{hint} — rate limited, not retrying immediately",)
                raise
            last_exc = e
            if attempt < retries:
                time.sleep(backoff)
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff)
    raise last_exc


def fetch_gdelt_news_for_company(company: str, max_records: int = 8) -> Tuple[List[Dict], Optional[str]]:
    """Search GDELT's free DOC 2.0 API for layoff/restructuring news about `company`."""
    cached = _get_cached_articles("GDELT", company, max_records)
    if cached is not None:
        logger.info(f"GDELT cache hit for '{company}' (<{NEWS_CACHE_TTL_HOURS}h old) — skipping live request")
        return cached, None

    articles = []
    params = {
        "query": f'"{company}" {LAYOFF_QUERY_TERMS}',
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_records,
        "sort": "hybridrel",
        "timespan": "3months",
    }
    try:
        _throttle_gdelt()
        resp = _get_with_retry(GDELT_DOC_URL, params, timeout=45, retries=1, backoff=2.0)
        data = resp.json() if resp.text.strip() else {}
        for a in (data.get("articles") or []):
            articles.append({
                "provider":        "GDELT",
                "title":           a.get("title", "") or "",
                "url":             a.get("url", "") or "",
                "published_date":  a.get("seendate", "") or "",
                "source_name":     a.get("domain", "") or "",
                "snippet":         "",
            })
        _set_cached_articles("GDELT", company, max_records, articles)
        return articles, None
    except requests.RequestException as e:
        msg = f"GDELT search failed for '{company}': {type(e).__name__}: {e}"
        logger.warning(msg)
        return articles, msg
    except ValueError as e:
        msg = f"GDELT returned invalid JSON for '{company}': {e}"
        logger.warning(msg)
        return articles, msg


def fetch_newsapi_news_for_company(company: str, max_records: int = 8) -> Tuple[List[Dict], Optional[str]]:
    """Search NewsAPI.org's /v2/everything endpoint for layoff news about `company`."""
    api_key = os.environ.get("NEWSAPI_KEY")
    if not api_key:
        msg = "NEWSAPI_KEY not set in the environment — NewsAPI search skipped"
        logger.warning(msg)
        return [], msg

    cached = _get_cached_articles("NewsAPI", company, max_records)
    if cached is not None:
        logger.info(f"NewsAPI cache hit for '{company}' (<{NEWS_CACHE_TTL_HOURS}h old) — skipping live request")
        return cached, None

    articles = []
    params = {
        "q":        f'"{company}" AND {LAYOFF_QUERY_TERMS}',
        "language": "en",
        "sortBy":   "relevancy",
        "pageSize": max_records,
        "apiKey":   api_key,
    }
    try:
        resp = _get_with_retry(NEWSAPI_URL, params, timeout=30, retries=1, backoff=2.0)
        data = resp.json() if resp.text.strip() else {}
        if data.get("status") != "ok":
            msg = f"NewsAPI returned non-ok status for '{company}': {data.get('message')}"
            logger.warning(msg)
            return [], msg
        for a in (data.get("articles") or []):
            source = a.get("source") or {}
            articles.append({
                "provider":        "NewsAPI",
                "title":           a.get("title", "") or "",
                "url":             a.get("url", "") or "",
                "published_date":  a.get("publishedAt", "") or "",
                "source_name":     source.get("name", "") or "",
                "snippet":         a.get("description", "") or a.get("content", "") or "",
            })
        _set_cached_articles("NewsAPI", company, max_records, articles)
        return articles, None
    except requests.RequestException as e:
        msg = f"NewsAPI search failed for '{company}': {type(e).__name__}: {e}"
        logger.warning(msg)
        return articles, msg
    except ValueError as e:
        msg = f"NewsAPI returned invalid JSON for '{company}': {e}"
        logger.warning(msg)
        return articles, msg


def fetch_article_text(url: str, max_chars: int = 4000) -> str:
    """Fetch and extract visible text from a news article URL. Best-effort:
    returns "" on any failure (paywalls, timeouts, non-HTML content)."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type:
            return ""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = html.unescape(text)

        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    except requests.RequestException as e:
        logger.debug(f"Article fetch failed for {url}: {e}")
        return ""
