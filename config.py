# WARN Layoff Monitor — Configuration

# How often the background scheduler re-screens the company list.
MONITOR_INTERVAL_HOURS = 168  # weekly

# Data source toggles
SOURCES = {
    "warntracker": True,   # WARNTracker.com per-company page (best-effort scrape)
    "gdelt": True,          # GDELT free news search, no API key required
    "newsapi": True,        # NewsAPI.org, requires NEWSAPI_KEY env var
}

# Company list — one name per line, comments (#) and blank lines ignored.
# Edit this file directly to add/remove companies; no code change needed.
COMPANY_LIST_PATH = "data/companies.txt"

# LLM classification model
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"

# Signal-rating thresholds are judgment calls made by the LLM classifier
# per event, not computed here — see classify/signal_classifier.py for the
# exact rubric (High / Medium / Watch / None).

# Epiq context fed to the classifier so it can write a grounded "Epiq angle"
# for each event, instead of generic commentary.
EPIQ_CONTEXT = """
Epiq Global provides legal services including:
- eDiscovery and managed review — document collection, processing, review for litigation
- Class action and mass tort administration — settlement administration, claims processing
- Bankruptcy and restructuring services — claims management, noticing, disbursement
- Legal operations consulting — process improvement, spend analysis
- Epiq Counsel — outside counsel management platform

Layoff triggers that create Epiq-relevant work:
- Corporate restructurings -> employment litigation, WARN Act litigation, eDiscovery
- Post-merger integrations -> document retention, legal ops consolidation
- PE-backed restructurings -> bankruptcy risk, claims administration
- Data breaches -> class action risk, eDiscovery, notification services
- Executive departures -> litigation risk, document preservation
- AI-related workforce reductions -> emerging regulatory/litigation exposure
"""

# Critical filter reminder for the classifier prompt: only corporate/HQ/
# white-collar events count. Plant, distribution, warehouse, retail-store,
# and union facility closures must be excluded (flagged as not-corporate,
# not surfaced as an event).
CORPORATE_FILTER_NOTE = """
ONLY corporate, HQ, or white-collar roles count as an event: finance, legal,
HR, IT, marketing, operations, shared services, post-merger restructuring,
executive reorganizations. EXCLUDE plant/manufacturing closures, distribution
or warehouse closures, retail store closures, and union facility shutdowns —
these are not corporate signals even if layoffs are confirmed.
"""
