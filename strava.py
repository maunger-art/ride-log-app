import time
from urllib.parse import urlencode

import requests

from config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REDIRECT_URI

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

def _require_strava_config() -> None:
    missing = []
    if not STRAVA_CLIENT_ID:
        missing.append("STRAVA_CLIENT_ID")
    if not STRAVA_CLIENT_SECRET:
        missing.append("STRAVA_CLIENT_SECRET")
    if not STRAVA_REDIRECT_URI:
        missing.append("STRAVA_REDIRECT_URI")
    if missing:
        raise ValueError(f"Missing Strava configuration: {', '.join(missing)}")

def build_auth_url(state: str, scope: str = "activity:read_all") -> str:
    _require_strava_config()
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": STRAVA_REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": scope,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"

def exchange_code_for_token(code: str) -> dict:
    _require_strava_config()
    r = requests.post(TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=20)
    r.raise_for_status()
    return r.json()

def refresh_access_token(refresh_token: str) -> dict:
    _require_strava_config()
    r = requests.post(TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=20)
    r.raise_for_status()
    return r.json()

def ensure_fresh_token(token_row):
    access_token, refresh_token, expires_at, athlete_id, scope = token_row
    now = int(time.time())
    if expires_at and now < int(expires_at) - 120:
        return access_token, refresh_token, int(expires_at), athlete_id, scope, False

    data = refresh_access_token(refresh_token)
    return (
        data["access_token"],
        data["refresh_token"],
        int(data["expires_at"]),
        data.get("athlete", {}).get("id"),
        data.get("scope"),
        True
    )

def list_activities(access_token: str, after_epoch: int, per_page: int = 50, page: int = 1):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"after": after_epoch, "per_page": per_page, "page": page}
    r = requests.get(ACTIVITIES_URL, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()
