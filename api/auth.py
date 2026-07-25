"""
Auth for the WARN Layoff Monitor API.

Two independent mechanisms, matching the GRI Screener's pattern but split
by audience:

  - HTTP Basic Auth guards the dashboard and read endpoints (/, /report,
    /companies GET) — set DASHBOARD_USER / DASHBOARD_PASS to require it.
    If unset, these routes are open (fine for a private Railway URL you
    haven't shared yet, but set both before handing the link to colleagues).

  - X-API-Key guards admin actions (/refresh, /companies POST, /audit).
    Keys are loaded from WARN_API_KEYS, format "key:role,key:role".
    Roles: admin (full access), reviewer (read-only via API key instead
    of Basic Auth, useful for scripts/automation).
"""
import os
import secrets
from fastapi import Security, HTTPException, status, Depends
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_basic = HTTPBasic(auto_error=False)

_DEFAULT_KEYS = {
    "warn-dev-key-12345": "admin",
}


def _load_keys() -> dict:
    raw = os.environ.get("WARN_API_KEYS", "")
    if not raw:
        return _DEFAULT_KEYS
    keys = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, r = pair.strip().split(":", 1)
            keys[k] = r
    return keys or _DEFAULT_KEYS


def get_current_role(api_key: str = Security(_api_key_header)) -> str:
    keys = _load_keys()
    if not api_key or api_key not in keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Pass X-API-Key header.",
        )
    return keys[api_key]


def require_admin(role: str = Security(get_current_role)):
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return role


def require_dashboard_auth(credentials: HTTPBasicCredentials = Depends(_basic)):
    """Gate the dashboard/report/companies-read routes with HTTP Basic Auth,
    but only if DASHBOARD_USER and DASHBOARD_PASS are both set. Uses
    constant-time comparison to avoid timing side-channels on the check."""
    expected_user = os.environ.get("DASHBOARD_USER")
    expected_pass = os.environ.get("DASHBOARD_PASS")
    if not expected_user or not expected_pass:
        return "open"  # no auth configured — route is public

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )

    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
