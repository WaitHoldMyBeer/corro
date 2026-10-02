"""Clio OAuth 2.0 authorization-code flow, and where the tokens are kept.

This file holds the only non-GET request the app sends to a Clio host: the
token exchange POST to /oauth/token. It is the OAuth server, not the Manage
API; it creates and changes nothing in the firm's account. Tokens are stored in
the SQLite database under `data/` (gitignored), never in a tracked file.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..config import Settings
from ..db import get_setting, now_iso, set_setting


class NotAuthorized(RuntimeError):
    """No usable Clio token: the firm has not connected Clio yet."""


def authorize_url(settings: Settings, conn: sqlite3.Connection) -> str:
    """The Clio page the attorney opens to grant this app read access."""
    if not settings.clio_client_id:
        raise NotAuthorized("CLIO_CLIENT_ID is not set. Copy .env.example to .env and fill it in.")
    state = secrets.token_urlsafe(24)
    set_setting(conn, "oauth_state", state)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.clio_client_id,
            "redirect_uri": settings.clio_redirect_uri,
            "state": state,
        }
    )
    return f"{settings.clio_base_url}/oauth/authorize?{query}"


def exchange_code(
    settings: Settings, conn: sqlite3.Connection, code: str, state: str | None, pasted: bool = False
) -> None:
    """Trade the one-time code for tokens and store them.

    `pasted` is the fallback for a redirect URI that shows the code on Clio's own
    page instead of calling us back: the person at the keyboard copies it in, so
    there is no state parameter to check."""
    expected = get_setting(conn, "oauth_state")
    if not pasted and (not expected or not state or not secrets.compare_digest(expected, state)):
        raise NotAuthorized("OAuth state mismatch; start again from the authorize link.")
    _token_request(
        settings,
        conn,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.clio_redirect_uri},
    )
    set_setting(conn, "oauth_state", "")


def refresh(settings: Settings, conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT refresh_token FROM oauth_tokens WHERE id=1").fetchone()
    if not row or not row["refresh_token"]:
        raise NotAuthorized("No Clio refresh token stored; connect Clio again.")
    return _token_request(settings, conn, {"grant_type": "refresh_token", "refresh_token": row["refresh_token"]})


def access_token(settings: Settings, conn: sqlite3.Connection) -> str:
    """A valid access token, refreshed if it is about to expire."""
    row = conn.execute("SELECT access_token, expires_at FROM oauth_tokens WHERE id=1").fetchone()
    if not row:
        raise NotAuthorized("Clio is not connected. Run `uv run python -m server auth`.")
    if row["expires_at"]:
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if expires - datetime.now(timezone.utc) < timedelta(minutes=5):
            return refresh(settings, conn)
    return row["access_token"]


def is_connected(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM oauth_tokens WHERE id=1").fetchone() is not None


def _token_request(settings: Settings, conn: sqlite3.Connection, grant: dict[str, str]) -> str:
    response = httpx.post(
        f"{settings.clio_base_url}/oauth/token",
        data={"client_id": settings.clio_client_id, "client_secret": settings.clio_client_secret, **grant},
        timeout=30.0,
    )
    if response.status_code != 200:
        # Clio's error body names the problem (bad redirect URI, expired code); it holds no secret.
        raise NotAuthorized(f"Clio token endpoint returned {response.status_code}: {response.text[:300]}")
    body = response.json()
    expires_at = None
    if body.get("expires_in"):
        expires = datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))
        expires_at = expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    previous = conn.execute("SELECT refresh_token FROM oauth_tokens WHERE id=1").fetchone()
    refresh_token = body.get("refresh_token") or (previous["refresh_token"] if previous else None)
    conn.execute(
        "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at, obtained_at) VALUES (1,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET access_token=excluded.access_token,"
        " refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, obtained_at=excluded.obtained_at",
        (body["access_token"], refresh_token, expires_at, now_iso()),
    )
    conn.commit()
    return body["access_token"]
