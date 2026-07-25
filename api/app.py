"""
WARN Layoff Monitor FastAPI application.

Endpoints:
  GET  /                    Dashboard (HTML)
  GET  /health               Health check (no auth)
  GET  /report                Latest snapshot as JSON
  GET  /companies             Current company list
  POST /companies             Add or remove a company (admin)
  POST /refresh                Trigger an immediate re-check (admin)
  GET  /scheduler/status       Scheduler status (last/next run)
  GET  /alerts                 Recent new/escalated-signal alerts
  GET  /audit                  Audit log (admin)
"""

import os
import sys
import logging
import threading
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from api.auth import require_admin, require_dashboard_auth
from monitoring.scheduler import MonitoringScheduler, load_company_list, save_company_list
from monitoring.audit import log_event, read_audit_log, EV_COMPANY_ADDED, EV_COMPANY_REMOVED
from config import MONITOR_INTERVAL_HOURS

logger = logging.getLogger(__name__)

_scheduler = MonitoringScheduler(poll_interval_seconds=3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _scheduler.start()
    logger.info(f"Scheduler started — checking every {MONITOR_INTERVAL_HOURS}h, first run imminent")
    yield
    _scheduler.stop()


app = FastAPI(
    title="WARN Act Weekly Layoff Monitor",
    version="1.0.0",
    description="Automated corporate-layoff and restructuring tracker for Epiq sales research.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(auth=Depends(require_dashboard_auth)):
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path) as f:
            return f.read()
    return HTMLResponse("<h2>Dashboard not found. See /docs for API reference.</h2>")


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/report", tags=["Report"], summary="Latest layoff-signal report")
def get_report(auth=Depends(require_dashboard_auth)):
    return _scheduler.get_latest_report()


@app.get("/alerts", tags=["Report"], summary="Recent new/escalated-signal alerts")
def get_alerts(limit: int = 100, auth=Depends(require_dashboard_auth)):
    return _scheduler.list_recent_alerts(limit=limit)


@app.get("/scheduler/status", tags=["System"], summary="Scheduler run status")
def scheduler_status(auth=Depends(require_dashboard_auth)):
    return _scheduler.status()


# ── Companies ─────────────────────────────────────────────────────────────────

@app.get("/companies", tags=["Companies"], summary="Current target company list")
def get_companies(auth=Depends(require_dashboard_auth)):
    return {"companies": load_company_list()}


@app.post("/companies", tags=["Companies"], summary="Add or remove a company")
def modify_companies(
    action: str = Body(..., embed=True, description='"add" or "remove"'),
    company: str = Body(..., embed=True),
    role: str = Depends(require_admin),
):
    companies = load_company_list()
    company = company.strip()

    if action == "add":
        if company not in companies:
            companies.append(company)
            save_company_list(companies)
            log_event(EV_COMPANY_ADDED, actor=role, company=company)
        return {"message": f"'{company}' added", "companies": companies}

    elif action == "remove":
        if company in companies:
            companies.remove(company)
            save_company_list(companies)
            log_event(EV_COMPANY_REMOVED, actor=role, company=company)
        return {"message": f"'{company}' removed", "companies": companies}

    raise HTTPException(status_code=400, detail='action must be "add" or "remove"')


# ── Admin actions ─────────────────────────────────────────────────────────────

@app.post("/refresh", tags=["Admin"], summary="Trigger an immediate re-check")
def trigger_refresh(role: str = Depends(require_admin)):
    def _bg():
        _scheduler.run_now(actor=role)
    threading.Thread(target=_bg, daemon=True).start()
    return {"message": "Refresh triggered in background — check /scheduler/status or /report shortly"}


@app.get("/audit", tags=["Admin"], summary="View audit log")
def get_audit_log(limit: int = 100, event_type: str = None, role: str = Depends(require_admin)):
    return read_audit_log(limit=limit, event_type=event_type)
