"""Clio -> SQLite, read-only.

Pulls one matter and everything hanging off it, stores each object verbatim with
a content hash, and downloads document bytes into `data/documents/`. Running it
again changes nothing unless Clio changed: the report then shows zeros.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clio import resources as res
from .clio.client import ClioClient, ClioError
from .clio.oauth import access_token, refresh
from .config import Settings
from .db import items, mark_removed, now_iso, upsert_item
from .pages import text_layer_stats


@dataclass
class KindReport:
    total: int = 0
    new: int = 0
    updated: int = 0
    removed: int = 0


@dataclass
class SyncReport:
    matter_id: int
    kinds: dict[str, KindReport] = field(default_factory=dict)
    documents_downloaded: int = 0
    bytes_downloaded: int = 0
    requests: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return sum(k.new + k.updated + k.removed for k in self.kinds.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "matter_id": self.matter_id,
            "kinds": {k: vars(v) for k, v in self.kinds.items()},
            "documents_downloaded": self.documents_downloaded,
            "bytes_downloaded": self.bytes_downloaded,
            "requests": self.requests,
            "changed": self.changed,
            "warnings": self.warnings,
        }


def make_client(settings: Settings, conn: sqlite3.Connection, quick: bool = False) -> ClioClient:
    """`quick` is for a page load: one attempt, short timeout, so an unreachable Clio
    falls back to what we hold instead of hanging."""
    return ClioClient(
        settings.api_base,
        get_access_token=lambda: access_token(settings, conn),
        refresh_access_token=lambda: refresh(settings, conn),
        attempts=1 if quick else None,
        timeout=2.0 if quick else 60.0,
    )


def list_matters(client: ClioClient) -> list[dict[str, Any]]:
    """Every matter the connected account can see: the picker reads this."""
    return list(client.get_all("matters.json", {"fields": res.MATTER_LIST_FIELDS}))


def sync_matter(
    settings: Settings, conn: sqlite3.Connection, client: ClioClient, matter_id: int, download: bool = True
) -> SyncReport:
    started = now_iso()
    run_id = conn.execute(
        "INSERT INTO sync_runs (matter_id, started_at) VALUES (?,?)", (matter_id, started)
    ).lastrowid
    conn.commit()
    report = SyncReport(matter_id)
    try:
        _sync(settings, conn, client, matter_id, run_id, started, report, download)
    except Exception as error:
        conn.rollback()
        conn.execute(
            "UPDATE sync_runs SET finished_at=?, error=? WHERE id=?", (now_iso(), type(error).__name__, run_id)
        )
        conn.commit()
        raise
    report.requests = client.requests_sent
    conn.execute(
        "UPDATE sync_runs SET finished_at=?, requests=?, report=? WHERE id=?",
        (now_iso(), report.requests, json.dumps(report.to_json()), run_id),
    )
    conn.commit()
    return report


def _sync(settings, conn, client, matter_id, run_id, at, report, download) -> None:
    def store(kind: str, rows: list[dict[str, Any]]) -> None:
        tally = report.kinds.setdefault(kind, KindReport())
        for row in rows:
            outcome = upsert_item(conn, run_id, matter_id, kind, row, at)
            tally.total += 1
            if outcome == "new":
                tally.new += 1
            elif outcome == "updated":
                tally.updated += 1
        tally.removed += mark_removed(conn, run_id, matter_id, kind, [r["id"] for r in rows], at)

    matter = client.get(f"matters/{matter_id}.json", {"fields": res.MATTER_FIELDS})["data"]
    store("matter", [matter])

    for resource in res.MATTER_RESOURCES:
        try:
            rows = list(client.get_all(resource.path, {"fields": resource.fields, **resource.params(matter_id)}))
        except ClioError as error:
            if not resource.optional:
                raise
            report.warnings.append(f"{resource.kind}: Clio returned {error.status}, skipped")
            continue
        if resource.kind_of is None:
            store(resource.kind, rows)
        else:
            for kind in resource.all_kinds():
                store(kind, [r for r in rows if resource.kind_of(r) == kind])

    # Contacts are account-wide in Clio; a matter's contacts are its client, its
    # related contacts, and whoever appears on its communications.
    contact_ids: set[int] = set()
    if matter.get("client"):
        contact_ids.add(int(matter["client"]["id"]))
    for relationship in items(conn, matter_id, "relationship"):
        if relationship.get("contact"):
            contact_ids.add(int(relationship["contact"]["id"]))
    for communication in items(conn, matter_id, "communication"):
        for party in (communication.get("senders") or []) + (communication.get("receivers") or []):
            if party.get("type") != "User":  # a contact: Clio gives its own type, Person or Company
                contact_ids.add(int(party["id"]))
    contacts = []
    if contact_ids:
        contacts = list(
            client.get_all("contacts.json", {"fields": res.CONTACT_FIELDS, "ids[]": sorted(contact_ids)})
        )
    for missing in sorted(contact_ids - {int(c["id"]) for c in contacts}):
        report.warnings.append(f"contact {missing} is referenced by the matter but Clio did not return it")
    store("contact", contacts)
    conn.commit()

    if download:
        _download_documents(settings, conn, client, matter_id, report)
        conn.commit()


def _version_key(document: dict[str, Any]) -> str:
    """Identifies the bytes: a new upload in Clio gets a new document version."""
    version = document.get("latest_document_version") or {}
    return str(version.get("uuid") or version.get("id") or document.get("etag") or document.get("updated_at"))


def _download_documents(settings, conn, client, matter_id, report) -> None:
    for document in items(conn, matter_id, "document"):
        document_id = int(document["id"])
        version_key = _version_key(document)
        row = conn.execute(
            "SELECT version_key, path FROM document_blobs WHERE matter_id=? AND document_id=?",
            (matter_id, document_id),
        ).fetchone()
        if row and row["version_key"] == version_key and (settings.data_dir / row["path"]).exists():
            continue
        suffix = Path(document.get("filename") or document.get("name") or "").suffix.lower() or ".bin"
        relative = Path("documents") / str(matter_id) / f"{document_id}{suffix}"
        try:
            sha256, size = client.download(f"documents/{document_id}/download.json", settings.data_dir / relative)
        except ClioError as error:
            report.warnings.append(f"document {document_id} download failed with status {error.status}")
            continue
        except httpx.TransportError as error:
            report.warnings.append(f"document {document_id} download failed: {type(error).__name__}")
            continue
        page_count, text_pages = text_layer_stats(settings.data_dir / relative)
        conn.execute(
            "INSERT INTO document_blobs (matter_id, document_id, version_key, sha256, size_bytes, path,"
            " content_type, downloaded_at, page_count, text_pages) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(matter_id, document_id) DO UPDATE SET version_key=excluded.version_key,"
            " sha256=excluded.sha256, size_bytes=excluded.size_bytes, path=excluded.path,"
            " content_type=excluded.content_type, downloaded_at=excluded.downloaded_at,"
            " page_count=excluded.page_count, text_pages=excluded.text_pages",
            (
                matter_id, document_id, version_key, sha256, size, str(relative),
                document.get("content_type"), now_iso(), page_count, text_pages,
            ),
        )
        conn.commit()
        report.documents_downloaded += 1
        report.bytes_downloaded += size
