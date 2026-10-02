"""The live checker: one engine that tests a sentence against the case's claims
ledger. `check_text` is the plain function; `api.router` is the HTTP route."""

from .engine import check, check_text, stats

__all__ = ["check", "check_text", "stats"]
