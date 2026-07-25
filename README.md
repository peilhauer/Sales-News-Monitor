# WARN Layoff Monitor

Weekly automated tracker for corporate-level layoffs and restructuring events
across a target company list, built for Epiq Global sales-trigger research.
Same shape as the GRI Screener project: FastAPI + uvicorn, deployed to
Railway from a Procfile, flat-file JSON storage, a background scheduler for
recurring runs, and a browser dashboard.

## What it does

Every week (configurable in `config.py`), the scheduler re-checks every
company in `data/companies.txt` against:
- **WARNTracker.com** — per-company WARN filing lookup (best-effort scrape)
- **GDELT** — free news search, no API key required
- **NewsAPI.org** — broader news search (requires `NEWSAPI_KEY`)

Findings are handed to an LLM classifier (`classify/signal_classifier.py`,
via the Anthropic API) that:
1. Filters out non-corporate events (plant, warehouse, retail, union
   facility closures) per the same corporate-only rule used in the manual
   report.
2. Rates each remaining event **High / Medium / Watch**.
3. Writes a 1–2 sentence "Epiq angle" grounded in Epiq's actual service
   lines (see `EPIQ_CONTEXT` in `config.py`).

Each week's results are saved as a snapshot under `data/monitoring/snapshots/`
and diffed against the prior week to flag new or escalated events —
mirroring `monitoring/scheduler.py` in the GRI Screener.

## Setup

```bash
cd warn_tracker
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
python server.py --reload
```

Visit `http://127.0.0.1:8000/` for the dashboard, `/docs` for the API.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Powers the signal classifier |
| `NEWSAPI_KEY` | No | Enables the NewsAPI connector (GDELT works without it) |
| `WARN_API_KEYS` | No | Admin API keys for `/refresh` and company-list edits, format `key:role,key:role` (defaults to a dev key — change before sharing the URL) |
| `DASHBOARD_USER` / `DASHBOARD_PASS` | No | HTTP Basic Auth on the dashboard, for sharing with colleagues without handing out an API key |

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | Basic (if set) | Dashboard |
| GET | `/health` | none | Health check |
| GET | `/report` | Basic (if set) | Latest snapshot as JSON |
| POST | `/refresh` | API key (admin) | Trigger an immediate re-check in the background |
| GET | `/companies` | Basic (if set) | Current company list |
| POST | `/companies` | API key (admin) | Add or remove a company |

## Deploying to Railway

See the accompanying "WARN Layoff Monitor — Deployment Next Steps" document
for the exact commands. In short:

1. Push this folder to its own new GitHub repo.
2. Create a new Railway service from that repo. Railway auto-detects the
   `Procfile`.
3. Set the environment variables above in the Railway service settings.
4. Deploy. The scheduler starts automatically with the app and runs the
   first check immediately, then weekly thereafter.

## Project structure

```
warn_tracker/
├── server.py                  # Entry point
├── config.py                  # Settings + Epiq context for the classifier
├── requirements.txt
├── data/
│   └── companies.txt           # Editable target company list
├── connectors/
│   ├── warntracker.py          # WARNTracker.com per-company lookup
│   └── news.py                 # GDELT + NewsAPI search, article text fetch
├── classify/
│   └── signal_classifier.py    # LLM: corporate filter + signal rating + Epiq angle
├── monitoring/
│   ├── scheduler.py            # Weekly run + snapshot diffing
│   ├── cache.py                # News-cache TTL layer
│   └── audit.py                # Append-only audit log
└── api/
    ├── app.py                  # FastAPI routes
    ├── auth.py                 # API-key + Basic Auth
    └── dashboard.html          # Browser dashboard
```
