"""
Audit logging.

Structured, append-only audit trail for scheduler runs and admin actions.
Ported near-verbatim from the GRI Screener's monitoring/audit.py.
"""

import json, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AUDIT_DIR  = Path("data/audit")
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"

EV_RUN_STARTED    = "run_started"
EV_RUN_COMPLETED  = "run_completed"
EV_RUN_FAILED     = "run_failed"
EV_ALERT_RAISED   = "alert_raised"
EV_COMPANY_ADDED  = "company_added"
EV_COMPANY_REMOVED = "company_removed"
EV_AUTH_FAILURE   = "auth_failure"


def _ensure_dir():
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def log_event(event_type: str, actor: str = "system", outcome: str = "ok", **kwargs):
    """Write a structured audit event to the audit log. Thread-safe via file append."""
    _ensure_dir()
    entry = {
        "ts":      datetime.now(timezone.utc).isoformat(),
        "event":   event_type,
        "actor":   actor,
        "outcome": outcome,
        **{k: v for k, v in kwargs.items() if v is not None},
    }
    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.error(f"Audit log write failed: {e}")


def read_audit_log(limit: int = 200, event_type: Optional[str] = None) -> list:
    """Read and optionally filter the audit log. Returns newest-first."""
    if not AUDIT_FILE.exists():
        return []
    try:
        lines = AUDIT_FILE.read_text().strip().splitlines()
        entries = []
        for line in reversed(lines[-5000:]):
            try:
                e = json.loads(line)
                if event_type and e.get("event") != event_type:
                    continue
                entries.append(e)
                if len(entries) >= limit:
                    break
            except Exception:
                continue
        return entries
    except Exception as e:
        logger.warning(f"Audit log read failed: {e}")
        return []
