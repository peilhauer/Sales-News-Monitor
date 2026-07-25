"""
LLM-based classification of gathered WARN filings + news into a rated,
corporate-filtered layoff signal — the automated equivalent of the manual
research + rating pass done in chat.

This is explicitly a heuristic aid for a human sales reviewer, not a legal
determination. Every event surfaced still needs a look before it goes into
outreach — same caveat the manual report carried.

Ported from the same pattern as GRI Screener's matching/news_resolver.py:
a single LLM call per subject (here, per company) that returns strict JSON,
with None on any failure so callers treat "classifier unavailable" as
"no signal this run," never as a crash.
"""

import os
import json
import logging
from typing import Dict, List, Optional

from config import EPIQ_CONTEXT, CORPORATE_FILTER_NOTE, CLASSIFIER_MODEL

logger = logging.getLogger(__name__)

_client = None

SIGNAL_RUBRIC = """
Signal rating rubric:
- "high"   — confirmed WARN filing OR large-scale announced corporate cuts
             (500+ roles), especially post-merger or PE-backed restructuring.
- "medium" — confirmed smaller WARN filing (fewer than 500 corporate roles)
             or announced corporate cuts without a WARN filing yet.
- "watch"  — credible signal (employee reports, earnings commentary,
             restructuring charges in an SEC filing) but no confirmed filing
             or official company statement.
- "none"   — nothing found, or everything found is non-corporate (plant,
             warehouse, retail, or union facility activity) and must be
             excluded per the corporate-only filter below.
"""


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — signal classification disabled")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — signal classification disabled")
        return None

    try:
        _client = anthropic.Anthropic(api_key=api_key)
        return _client
    except Exception as e:
        # Client construction can fail for reasons unrelated to the package
        # being missing (e.g. an environment-level proxy misconfiguration
        # raising ImportError from inside httpx's transport setup) — log the
        # real cause instead of relabeling it as "not installed".
        logger.warning(f"Anthropic client construction failed — signal classification disabled: {e}")
        return None


def classify_company_signal(
    company: str,
    warn_filings: List[Dict],
    articles: List[Dict],
) -> Optional[Dict]:
    """
    Given a company name, whatever WARNTracker filings were found, and news
    articles (each with a fetched or snippet-level "text" key), ask Claude
    to filter for corporate-only relevance, rate the signal, and draft an
    Epiq angle.

    Returns None if the LLM is unavailable or the call/parse fails — the
    caller should treat that as "no result this run," not as an error to
    surface as a false "none" rating.
    """
    client = _get_client()
    if client is None:
        return None

    if not warn_filings and not articles:
        return {
            "company": company, "corporate": False, "signal": "none",
            "what_happened": "", "epiq_angle": "", "source_name": "",
            "source_url": "", "source_date": "",
        }

    filings_block = json.dumps(warn_filings[:10], default=str) if warn_filings else "[]"
    articles_block = "\n\n".join(
        f"- Title: {a.get('title','')}\n"
        f"  Source: {a.get('source_name','')}\n"
        f"  Published: {a.get('published_date','')}\n"
        f"  URL: {a.get('url','')}\n"
        f"  Text: {(a.get('text') or a.get('snippet') or '')[:1500]}"
        for a in articles[:8]
    ) or "(none)"

    prompt = f"""You are assisting a sales research analyst at Epiq Global's legal-services
business, not making a legal determination.

{CORPORATE_FILTER_NOTE}

{SIGNAL_RUBRIC}

{EPIQ_CONTEXT}

Company being checked: {company}

WARNTracker.com filing data found (JSON, may be empty):
{filings_block}

News articles found (may be empty):
{articles_block}

Respond ONLY with a JSON object with these exact keys:
  "corporate": true or false — true only if there is a genuinely corporate/HQ/white-collar
    event described above (not exclusively plant, warehouse, retail, or union facility activity).
  "signal": one of "high", "medium", "watch", "none" per the rubric above. Use "none" if
    corporate is false or nothing relevant was found.
  "what_happened": 1-2 sentences with specific scope, location, roles, and trigger, or ""
    if signal is "none".
  "epiq_angle": 1-2 sentences on why this matters for Epiq specifically — which legal
    workstream it plausibly creates (employment litigation, eDiscovery, managed review,
    bankruptcy/claims administration, WARN Act litigation, document retention, etc.), grounded
    in the Epiq context above. "" if signal is "none".
  "source_name": the single best source name backing this, or "".
  "source_url": the matching URL, or "".
  "source_date": the article/filing date if available, or "".

Be conservative: if the only evidence is a plant, distribution, warehouse, or retail-store
closure, or a union facility shutdown, set corporate to false and signal to "none" even if a
layoff is confirmed."""

    try:
        message = client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start == -1:
            logger.warning(f"Signal classifier returned non-JSON response for '{company}'")
            return None

        result = json.loads(raw[json_start:json_end])
        if "signal" not in result:
            return None
        result["company"] = company
        return result

    except Exception as e:
        logger.warning(f"Signal classification failed for '{company}': {e}")
        return None
