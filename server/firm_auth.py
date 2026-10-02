"""The firm's side of the API, closed to anyone without a firm session.

The provider page and the firm's dashboard are served from one origin, so a
provider holding a share link could otherwise call the firm's routes directly.
Every `/api/` path needs a signed session cookie, except the provider's own
routes under `/api/share/`, the public contract schema and the sign-in routes
answered here. Static files and the Clio OAuth routes are left reachable.

The passcode comes from `FIRM_PASSCODE`. If that is unset, one is generated,
kept in the data directory so it survives a restart, and printed to the server
console at start-up. This is one shared passcode for a single firm, not user
accounts: there is no per-person identity and no server-side list of sessions.

A session outlives a restart: the token carries its own expiry and is signed with
a key derived from the passcode, which is the same after the restart. Changing
the passcode ends every session. The cookie's name includes the port, because a
browser keeps cookies per host and not per port: two instances on one machine
(production and a staging copy) would otherwise overwrite each other's session,
and signing in to one would sign the user out of the other.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime
from functools import lru_cache
from json import JSONDecodeError

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings

# The cookie is named COOKIE_<port> (see `cookie_name`). The bare name is still read, so a
# session started before the port was part of the name carries on until it expires.
COOKIE = "firm_session"

# How long a sign-in lasts. A product default.
SESSION_SECONDS = 12 * 60 * 60

LOGIN, LOGOUT, SESSION = "/api/firm/login", "/api/firm/logout", "/api/firm/session"

# What a visitor without a firm session may call: the provider's link and nothing else of the case.
OPEN_PREFIXES = ("/api/share/",)
OPEN_PATHS = {"/api/contract.schema.json"}  # the contract's shape, the same file that is in the repository

# Wrong passcodes allowed per client address per window before sign-in is refused for the rest of it.
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 60

PASSCODE_FILE = "firm_passcode"

# The largest body a caller without a session may send. What such a caller legitimately posts is a
# passcode or a message of a few thousand characters; anything larger is refused before it is read.
OPEN_BODY_BYTES = 64_000


@lru_cache(maxsize=1)
def passcode() -> str:
    """The firm's passcode: from the environment, or generated once and kept beside the database."""
    configured = os.environ.get("FIRM_PASSCODE", "").strip()
    if configured:
        return configured
    path = get_settings().data_dir / PASSCODE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Created owner-readable only, and only if nothing is there: two processes starting together agree on one value.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text().strip()
    else:
        value = secrets.token_urlsafe(12)
        with os.fdopen(handle, "w") as file:
            file.write(value)
    print(f"FIRM_PASSCODE is not set. Sign in to the firm dashboard with this generated passcode: {value}", flush=True)
    return value


def _signature(body: str) -> str:
    # The key is derived from the passcode, so changing the passcode ends every session.
    key = hashlib.sha256(b"firm-session:" + passcode().encode()).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def issue(now: float | None = None) -> tuple[str, int]:
    """A session token and the time it expires (seconds since the epoch)."""
    expires = int(now if now is not None else time.time()) + SESSION_SECONDS
    body = f"{expires}.{secrets.token_urlsafe(9)}"
    return f"{body}.{_signature(body)}", expires


def valid(token: str | None, now: float | None = None) -> bool:
    """True for a token this server signed that has not expired."""
    if not token or token.count(".") != 2:
        return False
    body, _, signature = token.rpartition(".")
    if not hmac.compare_digest(signature.encode(), _signature(body).encode()):
        return False
    expires = body.split(".")[0]
    return expires.isdigit() and int(expires) > (now if now is not None else time.time())


def _same(given: str, expected: str) -> bool:
    """Constant-time comparison. Hashing first makes both sides the same length."""
    return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(expected.encode()).digest())


def needs_session(path: str) -> bool:
    """Whether a path belongs to the firm. Anything under /api/ does unless it is listed as open."""
    if path != "/api" and not path.startswith("/api/"):
        return False
    if path in OPEN_PATHS:
        return False
    # A path with a dot segment is never treated as open, whatever it starts with.
    plain = not {".", ".."} & set(path.split("/"))
    return not (plain and path.startswith(OPEN_PREFIXES))


def cookie_name(scope: Scope) -> str:
    """This instance's cookie: the shared name plus the port it is served on."""
    server = scope.get("server")
    return f"{COOKIE}_{server[1]}" if server and server[1] else COOKIE


def signed_in(connection: HTTPConnection) -> bool:
    """True when the request carries a session this instance signed, under its own cookie
    name or the older bare one."""
    cookies = connection.cookies
    return valid(cookies.get(cookie_name(connection.scope))) or valid(cookies.get(COOKIE))


def _oversized(scope: Scope) -> JSONResponse | None:
    """The refusal for a body that a caller without a session may not send, or None if it may.
    Decided from the headers alone, so nothing of the body is buffered or echoed back."""
    headers = {name.lower(): value for name, value in scope.get("headers") or []}
    length = headers.get(b"content-length")
    if length is None:
        if b"transfer-encoding" in headers:
            return _refused("Say how long the body is (Content-Length).", "length_required", 411)
        return None  # no body at all
    if not length.isdigit():
        return _refused("Content-Length is not a number.", "bad_length", 400)
    if int(length) > OPEN_BODY_BYTES:
        return _refused(f"The body is larger than {OPEN_BODY_BYTES} bytes.", "body_too_large", 413)
    return None


def _refused(detail: str, code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "code": code, "login": LOGIN}, status_code=status, headers={"Cache-Control": "no-store"}
    )


class FirmGuard:
    """ASGI middleware: answers the sign-in routes itself and turns away firm routes without a session."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.failures: dict[str, list[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if scope["type"] == "http" and scope["method"] in ("POST", "PUT", "PATCH") and not signed_in(HTTPConnection(scope)):
            # Without a session the only routes that take a body are the sign-in and a provider's own
            # link; both get the same small cap. A signed-in firm may send more (a layout, an upload).
            refusal = _oversized(scope)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        if scope["type"] == "http" and path in (LOGIN, LOGOUT, SESSION):
            response = await self._answer(Request(scope, receive), path)
            await response(scope, receive, send)
            return
        if needs_session(path) and not signed_in(HTTPConnection(scope)):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await _refused("Firm sign-in required.", "firm_session_required", 401)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _answer(self, request: Request, path: str) -> Response:
        if path == SESSION and request.method == "GET":
            return JSONResponse({"signed_in": signed_in(request)}, headers={"Cache-Control": "no-store"})
        if path == LOGOUT and request.method == "POST":
            response = JSONResponse({"signed_in": False})
            response.delete_cookie(cookie_name(request.scope), path="/")
            response.delete_cookie(COOKIE, path="/")
            return response
        if path == LOGIN and request.method == "POST":
            return await self._login(request)
        return JSONResponse({"detail": "Method not allowed."}, status_code=405)

    async def _login(self, request: Request) -> Response:
        client = request.client.host if request.client else "unknown"
        now = time.time()
        recent = [at for at in self.failures.get(client, []) if now - at < FAILURE_WINDOW_SECONDS]
        if len(recent) >= MAX_FAILURES:
            return _refused("Too many wrong passcodes. Wait a minute and try again.", "firm_login_throttled", 429)
        try:
            body = await request.json()
        except (JSONDecodeError, UnicodeDecodeError):
            body = None
        given = body.get("passcode") if isinstance(body, dict) else None
        if not isinstance(given, str) or not _same(given, passcode()):
            self.failures[client] = [*recent, now]
            return _refused("Wrong passcode.", "firm_login_failed", 401)
        self.failures.pop(client, None)
        token, expires = issue(now)
        response = JSONResponse(
            {"signed_in": True, "expires_at": datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            cookie_name(request.scope),
            token,
            max_age=SESSION_SECONDS,
            path="/",
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response


def install(app) -> None:
    """Put the guard in front of the whole app. Where this is called in app.py does not matter."""
    app.add_middleware(FirmGuard)
    passcode()  # so a generated passcode is printed when the server starts, not at the first sign-in
