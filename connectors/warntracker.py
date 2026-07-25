"""
WARNTracker.com per-company lookup.

WARNTracker.com doesn't publish a documented API, so this is a best-effort
scrape of each company's public page (e.g. warntracker.com/company/dow) —
it parses whatever WARN-filing table it finds and returns it as plain
dicts. Slug guessing is imperfect (company names don't map 1:1 to URLs),
so this connector is deliberately tolerant: a wrong slug or a changed page
layout returns an empty list plus an explicit error string, never a
silent crash. Treat this as one signal among several, not the sole source
of truth — the news connectors (connectors/news.py) cover the gap when a
slug guess misses.

A SLUG_OVERRIDES map is provided for companies where the obvious slug is
wrong (discovered by hand, same way the original research did it) — add
to it over time as you notice misses.
"""

import re
import logging
import requests
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://www.warntracker.com/company/{slug}"

HEADERS = {
    "User-Agent": "WARN-Layoff-Monitor/1.0 (sales research)",
}

# Hand-corrected slugs for companies whose obvious slugification doesn't
# match WARNTracker's actual URL. Extend this as misses are found.
SLUG_OVERRIDES = {
    "Kroger Co.": "the-kroger-co",
    "JM Smucker Company": "the-jm-smucker-company",
    "The Walsh Group": "walsh-group",
}


def _slugify(company: str) -> str:
    name = SLUG_OVERRIDES.get(company)
    if name:
        return name
    s = company.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[.,']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def fetch_warntracker_filings(company: str, timeout: int = 20) -> Tuple[List[Dict], Optional[str]]:
    """
    Fetch and parse the WARN-filing table for `company` from WARNTracker.com.

    Returns (filings, error). filings is a list of dicts with whatever
    columns the page's table actually has (commonly company, location,
    date, employees_affected, notice_type) — column names come straight
    from the page's own headers rather than an assumed schema, since the
    site's layout isn't a stable contract. error is None on success (even
    for zero filings — a real "no filings found" is not an error).
    """
    slug = _slugify(company)
    url = BASE_URL.format(slug=slug)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 404:
            return [], f"No WARNTracker page found at slug '{slug}' (guessed from '{company}')"
        resp.raise_for_status()
    except requests.RequestException as e:
        msg = f"WARNTracker request failed for '{company}' ({url}): {type(e).__name__}: {e}"
        logger.warning(msg)
        return [], msg

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return [], f"No filing table found on WARNTracker page for '{company}' ({url})"

        rows = table.find_all("tr")
        if not rows:
            return [], None

        header_cells = rows[0].find_all(["th", "td"])
        headers = [
            re.sub(r"\s+", "_", h.get_text(strip=True).lower()) or f"col{i}"
            for i, h in enumerate(header_cells)
        ]

        filings = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            values = [c.get_text(strip=True) for c in cells]
            record = dict(zip(headers, values))
            record["source_url"] = url
            filings.append(record)

        return filings, None

    except ImportError:
        return [], "beautifulsoup4 not installed — cannot parse WARNTracker page"
    except Exception as e:
        msg = f"WARNTracker parse failed for '{company}' ({url}): {type(e).__name__}: {e}"
        logger.warning(msg)
        return [], msg
