"""
Weekly WARN monitoring scheduler.

Re-checks every company in the target list on a configurable interval
(config.MONITOR_INTERVAL_HOURS, default weekly), gathers WARNTracker
filings + news, classifies each company's signal with the LLM, saves a
snapshot, and diffs against the prior snapshot to flag new or escalated
events. Ported from the shape of GRI Screener's monitoring/scheduler.py
(persistent watchlist re-screened on an interval, diffed vs prior run).

Storage layout (under data/monitoring/):
  snapshots/{timestamp}.json   - one snapshot per run, all companies
  alerts/alerts.jsonl          - append-only alert log (new/escalated signals)

Usage:
  from monitoring.scheduler import MonitoringScheduler
  sched = MonitoringScheduler()
  sched.start()             # runs in background thread, first run immediate
  sched.run_now()           # manual trigger, returns the new snapshot
  sched.get_latest_report() # dashboard-ready summary of the latest snapshot
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from config import (
    COMPANY_LIST_PATH, MONITOR_INTERVAL_HOURS, SOURCES,
)
from connectors.warntracker import fetch_warntracker_filings
from connectors.news import (
    fetch_gdelt_news_for_company, fetch_newsapi_news_for_company, fetch_article_text,
)
from classify.signal_classifier import classify_company_signal
from monitoring.audit import log_event, EV_RUN_STARTED, EV_RUN_COMPLETED, EV_RUN_FAILED, EV_ALERT_RAISED

logger = logging.getLogger(__name__)

BASE_DIR   = Path("data/monitoring")
SNAPSHOTS  = BASE_DIR / "snapshots"
ALERTS_DIR = BASE_DIR / "alerts"
ALERTS_FILE = ALERTS_DIR / "alerts.jsonl"

SIGNAL_RANK = {"high": 0, "medium": 1, "watch": 2, "none": 3}


def _ensure_dirs():
    for d in (SNAPSHOTS, ALERTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_company_list() -> List[str]:
    path = Path(COMPANY_LIST_PATH)
    if not path.exists():
        logger.warning(f"Company list not found at {path}")
        return []
    companies = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        companies.append(line)
    return companies


def save_company_list(companies: List[str]):
    path = Path(COMPANY_LIST_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(companies) + "\n")


class MonitoringScheduler:
    def __init__(self, poll_interval_seconds: int = 3600):
        _ensure_dirs()
        self._poll_interval = poll_interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running_lock = threading.Lock()
        self._is_running = False
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = datetime.now(timezone.utc)  # run immediately on first poll

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="warn-monitor")
        self._thread.start()
        logger.info("WARN monitoring scheduler started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("WARN monitoring scheduler stopped")

    def status(self) -> dict:
        return {
            "running_now": self._is_running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "interval_hours": MONITOR_INTERVAL_HOURS,
        }

    def _loop(self):
        while not self._stop_event.is_set():
            if self.next_run and datetime.now(timezone.utc) >= self.next_run:
                try:
                    self.run_now()
                except Exception as e:
                    logger.error(f"Scheduled run failed: {e}")
            self._stop_event.wait(timeout=self._poll_interval)

    # ── Core run ─────────────────────────────────────────────────────────────

    def run_now(self, actor: str = "system") -> dict:
        """Run one full pass over the company list. Safe to call manually
        (e.g. from POST /refresh) even while the background loop is active —
        guarded so two runs never overlap."""
        with self._running_lock:
            if self._is_running:
                return {"status": "already_running"}
            self._is_running = True

        try:
            log_event(EV_RUN_STARTED, actor=actor)
            ts = datetime.now(timezone.utc)
            companies = load_company_list()
            results = []

            for company in companies:
                results.append(self._check_company(company))

            snapshot = {"run_at": ts.isoformat(), "results": results}
            snap_path = SNAPSHOTS / f"{ts.strftime('%Y%m%d_%H%M%S')}.json"
            snap_path.write_text(json.dumps(snapshot, default=str))

            alerts = self._diff_snapshots(results, ts)
            if alerts:
                self._append_alerts(alerts)
                for a in alerts:
                    log_event(EV_ALERT_RAISED, actor="system",
                              company=a["company"], alert_type=a["alert_type"])

            self.last_run = ts
            self.next_run = ts + timedelta(hours=MONITOR_INTERVAL_HOURS)
            log_event(EV_RUN_COMPLETED, actor=actor,
                      company_count=len(companies), alert_count=len(alerts))
            logger.info(f"WARN run complete: {len(companies)} companies, {len(alerts)} new alerts")
            return {"status": "ok", "run_at": ts.isoformat(),
                    "company_count": len(companies), "alert_count": len(alerts)}

        except Exception as e:
            log_event(EV_RUN_FAILED, actor=actor, outcome="error", error=str(e))
            logger.exception("WARN monitoring run failed")
            raise
        finally:
            with self._running_lock:
                self._is_running = False

    def _check_company(self, company: str) -> dict:
        errors = []
        warn_filings: List[Dict] = []
        articles: List[Dict] = []

        if SOURCES.get("warntracker"):
            filings, err = fetch_warntracker_filings(company)
            warn_filings.extend(filings)
            if err:
                errors.append(f"warntracker: {err}")

        if SOURCES.get("gdelt"):
            hits, err = fetch_gdelt_news_for_company(company)
            articles.extend(hits)
            if err:
                errors.append(f"gdelt: {err}")

        if SOURCES.get("newsapi"):
            hits, err = fetch_newsapi_news_for_company(company)
            articles.extend(hits)
            if err:
                errors.append(f"newsapi: {err}")

        # De-dupe articles by URL, fetch full text for the top handful only
        # (keeps runs fast across ~80 companies; classifier falls back to
        # the snippet if full-text fetch comes back empty).
        seen_urls, deduped = set(), []
        for a in articles:
            if a["url"] and a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                deduped.append(a)
        for a in deduped[:6]:
            a["text"] = fetch_article_text(a["url"]) or a.get("snippet", "")

        classification = classify_company_signal(company, warn_filings, deduped)
        if classification is None:
            classification = {
                "company": company, "corporate": False, "signal": "none",
                "what_happened": "", "epiq_angle": "", "source_name": "",
                "source_url": "", "source_date": "",
            }

        classification["warn_filing_count"] = len(warn_filings)
        classification["article_count"] = len(deduped)
        classification["errors"] = errors
        return classification

    # ── Diffing ──────────────────────────────────────────────────────────────

    def _diff_snapshots(self, current_results: List[dict], run_at: datetime) -> List[dict]:
        prior = _load_prior_snapshot()
        if not prior:
            return []  # first run — nothing to diff against

        prior_by_company = {r["company"]: r for r in prior.get("results", [])}
        alerts = []

        for result in current_results:
            company = result["company"]
            current_signal = result.get("signal", "none")
            if current_signal == "none":
                continue

            prior_result = prior_by_company.get(company)
            prior_signal = prior_result.get("signal", "none") if prior_result else "none"

            if prior_signal == "none":
                alerts.append(_make_alert("new_signal", result, run_at))
            elif SIGNAL_RANK.get(current_signal, 3) < SIGNAL_RANK.get(prior_signal, 3):
                alerts.append(_make_alert("signal_escalated", result, run_at))

        return alerts

    def _append_alerts(self, alerts: List[dict]):
        with open(ALERTS_FILE, "a") as f:
            for alert in alerts:
                f.write(json.dumps(alert, default=str) + "\n")

    # ── Reporting for the API ───────────────────────────────────────────────

    def get_latest_report(self) -> dict:
        snapshot = _load_latest_snapshot()
        if not snapshot:
            return {
                "run_at": None, "active": [], "watch": [], "no_activity": [],
                "summary": {"active_count": 0, "watch_count": 0, "no_activity_count": 0},
            }

        results = snapshot.get("results", [])
        active = [r for r in results if r.get("signal") in ("high", "medium")]
        watch  = [r for r in results if r.get("signal") == "watch"]
        no_activity = [r["company"] for r in results if r.get("signal") == "none"]

        active.sort(key=lambda r: SIGNAL_RANK.get(r.get("signal"), 3))

        return {
            "run_at": snapshot.get("run_at"),
            "active": active,
            "watch": watch,
            "no_activity": no_activity,
            "summary": {
                "active_count": len(active),
                "watch_count": len(watch),
                "no_activity_count": len(no_activity),
            },
        }

    def list_recent_alerts(self, limit: int = 100) -> List[dict]:
        if not ALERTS_FILE.exists():
            return []
        lines = ALERTS_FILE.read_text().strip().splitlines()[-limit:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]


# ── Module-level helpers ──────────────────────────────────────────────────────

def _make_alert(alert_type: str, result: dict, run_at: datetime) -> dict:
    return {
        "alert_type": alert_type,
        "run_at": run_at.isoformat(),
        "company": result["company"],
        "signal": result.get("signal"),
        "what_happened": result.get("what_happened", ""),
        "source_url": result.get("source_url", ""),
    }


def _load_latest_snapshot() -> Optional[dict]:
    snaps = sorted(SNAPSHOTS.glob("*.json"))
    if not snaps:
        return None
    try:
        return json.loads(snaps[-1].read_text())
    except Exception:
        return None


def _load_prior_snapshot() -> Optional[dict]:
    snaps = sorted(SNAPSHOTS.glob("*.json"))
    if len(snaps) < 2:
        return None
    try:
        return json.loads(snaps[-2].read_text())
    except Exception:
        return None
