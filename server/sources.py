"""Opens a SourceRef: the note, email, task, field or document page behind a value."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from shared import contract as c

from . import pages, rules
from .case import CaseBuilder
from .config import Settings
from .db import item


def _names(people: list[dict[str, Any]] | None) -> list[str]:
    return [p.get("name") or f"{p.get('type', '')} {p.get('id', '')}".strip() for p in people or []]


def span_in(quote: str, text: str) -> str | None:
    """The stretch of `text` that is the quote, as the text itself writes it (its own
    case and line breaks), or None. Lets the UI highlight exactly what is there."""
    words = [re.escape(word) for word in quote.split()]
    if not words:
        return None
    found = re.search(r"\s+".join(words), text, re.IGNORECASE)
    return found.group(0) if found else None


def detail(
    cfg: Settings,
    build: CaseBuilder,
    kind: str,
    clio_id: str,
    page: int | None,
    quote: str | None,
    claim_id: str | None = None,
    claim_verified: bool = False,
) -> c.SourceDetail | None:
    matter_id, conn = build.matter_id, build.conn
    title, when, author, parties, text = "", None, None, [], None
    extra: dict[str, Any] = {}

    if kind == "custom_field":
        value = next(
            (
                v
                for v in build.matter.get("custom_field_values") or []
                if str((v.get("custom_field") or {}).get("id") or v.get("id")) == str(clio_id)
            ),
            None,
        )
        if value is None:
            return None
        title, when = value.get("field_name") or "", value.get("updated_at")
        text = "" if value.get("value") is None else str(value["value"])
    elif kind == "matter":
        row = build.matter
        title, when, text = row.get("display_number") or "", row.get("updated_at"), row.get("description")
    else:
        row = item(conn, matter_id, kind, clio_id)
        if row is None:
            return None
        if kind == "note":
            title, when, text = row.get("subject") or "", row.get("date"), row.get("detail")
            author = (row.get("author") or {}).get("name")
        elif kind == "communication":
            title, when, text = row.get("subject") or "", row.get("date"), row.get("body")
            author = ", ".join(_names(row.get("senders"))) or None
            parties = _names(row.get("senders")) + _names(row.get("receivers"))
        elif kind == "task":
            title, when, text = row.get("name") or "", row.get("due_at"), row.get("description")
            parties = _names([row["assignee"]] if row.get("assignee") else [])
        elif kind == "calendar_entry":
            title, when = row.get("summary") or "", row.get("start_at")
            text = "\n".join(part for part in (row.get("description"), row.get("location")) if part) or None
            parties = _names(row.get("attendees"))
        elif kind == "expense":
            title, when = row.get("note") or "", row.get("date")
            amount = rules.decimal_or_none(row.get("total"))
            text = "\n".join(
                part
                for part in (rules.usd(amount), (row.get("expense_category") or {}).get("name"), row.get("note"))
                if part
            )
            author = (row.get("user") or {}).get("name")
        elif kind == "contact":
            title, when = row.get("name") or "", row.get("updated_at")
            text = "\n".join(
                part for part in (row.get("title"), row.get("primary_email_address"), row.get("primary_phone_number")) if part
            ) or None
        elif kind == "relationship":
            title = (row.get("contact") or {}).get("name") or ""
            when, text = row.get("updated_at"), row.get("description")
        elif kind == "document":
            title, when = row.get("name") or row.get("filename") or "", row.get("received_at")
            path = pages.blob_path(cfg, conn, matter_id, int(row["id"]))
            if path is not None:
                extra["file_href"] = f"/api/matters/{matter_id}/documents/{row['id']}/file"
                if path.suffix.lower() == ".pdf":
                    shown = page or 1
                    extra["page_count"] = pages.page_count(path)
                    mark = f"?mark={quote_plus(claim_id)}" if claim_id and quote else ""
                    extra["page_image_href"] = f"/api/matters/{matter_id}/documents/{row['id']}/pages/{shown}.png{mark}"
                    text = pages.page_text(path, shown) or None

    # A quote is only ever marked as checked, or highlighted, when code finds it in the source text.
    # For a claim, the check is the one the digest stored; the quote is shown either way, with its flag.
    span = span_in(quote, text) if quote and text else None
    verified = claim_verified if claim_id else span is not None
    shown_quote = quote if (claim_id or verified) else None
    ref = build.ref(kind, clio_id, title, when, page=page, quote=shown_quote, quote_verified=verified)
    return c.SourceDetail(
        ref=ref,
        title=title,
        date=when,
        author=author,
        parties=parties,
        text=text,
        highlight=span,
        **extra,
    )
