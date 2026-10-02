"""Read-only Clio Manage API v4 client.

THE READ-ONLY GUARANTEE IS THIS FILE. Clio is an input to this app and nothing
else. `_refuse_anything_but_get` is installed as a request hook on every HTTP
client that talks to the Clio API or follows one of its download redirects, so
a POST, PUT, PATCH or DELETE raises before a byte leaves the machine. The class
below has no method that takes an HTTP verb.

(The one POST this app ever sends to a Clio host is the OAuth token exchange in
`oauth.py`. It goes to /oauth/token, not to the API, and carries no case data.)
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

READ_ONLY_METHODS = frozenset({"GET"})


class ClioWriteForbidden(RuntimeError):
    """Raised when anything tries to send a non-GET request to Clio."""


class ClioError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"Clio returned {status} for GET {url}: {body[:500]}")
        self.status = status
        self.body = body


def _refuse_anything_but_get(request: httpx.Request) -> None:
    if request.method.upper() not in READ_ONLY_METHODS:
        raise ClioWriteForbidden(
            f"{request.method} {request.url.path} blocked: this app only reads from Clio (GET)."
        )


class ClioClient:
    """GET, paginated GET, and document download. Nothing else."""

    PAGE_LIMIT = 200  # Clio's maximum page size
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        api_base: str,
        get_access_token: Callable[[], str],
        refresh_access_token: Callable[[], str] | None = None,
        transport: httpx.BaseTransport | None = None,
        attempts: int | None = None,
        timeout: float = 60.0,
    ):
        self.MAX_ATTEMPTS = attempts or self.MAX_ATTEMPTS
        self._api_base = api_base.rstrip("/")
        self._api_host = httpx.URL(self._api_base).host
        self._get_access_token = get_access_token
        self._refresh_access_token = refresh_access_token
        hooks = {"request": [_refuse_anything_but_get]}
        self._api = httpx.Client(timeout=timeout, event_hooks=hooks, transport=transport, follow_redirects=False)
        # Signed download URLs live on a storage host; they get no Authorization header.
        self._storage = httpx.Client(timeout=300.0, event_hooks=hooks, transport=transport, follow_redirects=True)
        self.requests_sent = 0

    def close(self) -> None:
        self._api.close()
        self._storage.close()

    # -- reads ---------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET one API path (or a full `next` page URL) and return the parsed JSON."""
        return self._get(self._url(path), params).json()

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """GET a list endpoint and follow `meta.paging.next` until it runs out."""
        url: str | None = self._url(path)
        query: dict[str, Any] | None = {"limit": self.PAGE_LIMIT, **(params or {})}
        while url:
            page = self._get(url, query).json()
            yield from page.get("data") or []
            url = ((page.get("meta") or {}).get("paging") or {}).get("next")
            query = None  # the next URL already carries the query

    def download(self, path: str, dest: Path) -> tuple[str, int]:
        """GET a document's bytes to `dest`. Returns (sha256, size in bytes).

        Clio answers the download path with a redirect to a signed storage URL.
        A dropped connection is retried from a fresh signed URL.
        """
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                return self._download_once(path, dest)
            except httpx.TransportError:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def _download_once(self, path: str, dest: Path) -> tuple[str, int]:
        response = self._get(self._url(path), None)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        digest, size = hashlib.sha256(), 0
        if response.is_redirect:
            self.requests_sent += 1
            with self._storage.stream("GET", response.headers["location"]) as stream:
                if stream.status_code != 200:
                    stream.read()
                    raise ClioError(stream.status_code, "<signed download url>", stream.text)
                with partial.open("wb") as out:
                    for chunk in stream.iter_bytes(1 << 16):
                        out.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
        else:
            partial.write_bytes(response.content)
            digest.update(response.content)
            size = len(response.content)
        partial.replace(dest)
        return digest.hexdigest(), size

    # -- plumbing ------------------------------------------------------------

    @staticmethod
    def _wait_if_budget_spent(response: httpx.Response) -> None:
        """Clio allows a fixed number of requests per minute per token and reports
        what is left. When it reaches zero, wait for the window to reset rather
        than earn a 429."""
        if response.headers.get("X-RateLimit-Remaining") != "0":
            return
        try:
            wait = float(response.headers["X-RateLimit-Reset"]) - time.time()
        except (KeyError, ValueError):
            return
        if 0 < wait <= 90:
            time.sleep(wait + 0.5)

    def _url(self, path: str) -> str:
        if not path.startswith("http"):
            return f"{self._api_base}/{path.lstrip('/')}"
        # A full URL (a `next` page link) gets the bearer token only if it is on Clio's API host.
        if httpx.URL(path).host != self._api_host:
            raise ClioError(0, path.split("?")[0], "refusing to send the Clio token to another host")
        return path

    def _get(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        refreshed = False
        for attempt in range(self.MAX_ATTEMPTS):
            self.requests_sent += 1
            headers = {"Authorization": f"Bearer {self._get_access_token()}", "Accept": "application/json"}
            try:
                response = self._api.get(url, params=params, headers=headers)
            except httpx.TransportError:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise
                time.sleep(2**attempt)
                continue
            if response.status_code == 401 and self._refresh_access_token and not refreshed:
                self._refresh_access_token()
                refreshed = True
                continue
            if response.status_code == 429 or response.status_code >= 500:
                # Clio rate-limits per token and says how long to wait.
                if self.MAX_ATTEMPTS == 1:  # a caller that will not retry must not be made to wait either
                    raise ClioError(response.status_code, str(response.request.url.path), response.text)
                try:
                    wait = float(response.headers.get("Retry-After") or 2**attempt)
                except ValueError:  # Retry-After may be an HTTP date
                    wait = 2**attempt
                time.sleep(min(wait, 60.0))
                continue
            if response.status_code >= 400:
                raise ClioError(response.status_code, str(response.request.url.path), response.text)
            if self.MAX_ATTEMPTS > 1:
                self._wait_if_budget_spent(response)
            return response
        raise ClioError(response.status_code, str(response.request.url.path), response.text)
