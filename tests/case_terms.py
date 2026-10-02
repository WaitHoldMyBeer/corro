"""Derives, at run time, the strings that must not appear in this repository.

Nothing in this file names a matter. The terms come from the gitignored
reference export of the demo matter (a `*-clio-data.json` file under an ignored
directory) and, when someone has synced, from the app's own SQLite cache of the
live matter, which can hold more than the export. In a clean clone neither
exists and `load()` returns None, so the checks that depend on it skip with a
visible notice.

Tiers:
  hard    - must not appear in any tracked file or commit message
  code    - must not appear in code, prompts, mocks or fixtures; warned in docs
  warn    - reported, never fatal (generic-looking, worth a human glance)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent

DICTIONARIES = (Path("/usr/share/dict/words"), Path("/usr/share/dict/american-english"))

# Generic vocabulary of the domain and of the tooling. None of it is a case fact;
# it only stops the proper-noun heuristic from flagging words every matter shares.
GENERIC = frozenset(
    """
    clio medicaid medicare hipaa erisa mva mri emg ct xray icd cpt pip um uim ime llc pllc inc corp
    january february march april may june july august september october november december
    monday tuesday wednesday thursday friday saturday sunday
    plaintiff defendant defendants plaintiffs esq attorney paralegal counsel court judge clerk
    intake retainer litigation discovery settlement insurance medical records bills correspondence
    pleadings liens lien subpoena deposition affidavit summons complaint stipulation
    email phone voicemail fax pdf http https www com org net
    """.split()
)

ENTITY_SUFFIX = re.compile(
    r",?\s+(p\.?l\.?l\.?c\.?|l\.?l\.?c\.?|p\.?c\.?|inc\.?|l\.?l\.?p\.?|corp\.?|co\.?|ltd\.?|p\.?a\.?|m\.?d\.?)$",
    re.IGNORECASE,
)
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
IDENTIFIER = re.compile(r"\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9][A-Z0-9/-]{5,}\b|\b\d{7,}\b")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
MONTHS = ("January February March April May June July August September October November December").split()


@dataclass(repr=False)
class Terms:
    hard: dict[str, str] = field(default_factory=dict)  # term -> class
    code: dict[str, str] = field(default_factory=dict)
    warn: dict[str, str] = field(default_factory=dict)
    matter_label: str = ""  # the public name of the demo matter; allowed in prose docs
    shingles: set[str] = field(default_factory=set)  # runs of words copied from free text

    def __repr__(self) -> str:
        # Never the terms themselves: a failing test prints its arguments.
        return f"Terms(hard={len(self.hard)}, code={len(self.code)}, warn={len(self.warn)}, shingles={len(self.shingles)})"

    def add(self, tier: dict[str, str], term: str | None, kind: str, min_len: int = 4) -> None:
        term = " ".join((term or "").split())
        if len(term) >= min_len:
            tier.setdefault(term, kind)


def reference_file() -> Path | None:
    """The reference export, found by shape rather than by name."""
    ignored = subprocess.run(
        ["git", "ls-files", "-o", "-i", "--exclude-standard", "--directory"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()
    for entry in ignored:
        base = ROOT / entry
        if base.is_dir():
            for found in sorted(base.glob("*-clio-data.json")):
                return found
    return None


def _bodies(node: Any) -> Iterator[dict[str, Any]]:
    """Every request body in the export; the explanatory wrapper keys are skipped."""
    if isinstance(node, dict):
        if isinstance(node.get("body"), dict):
            yield node["body"]
        for key, value in node.items():
            if key not in ("body", "about"):
                yield from _bodies(value)
    elif isinstance(node, list):
        for value in node:
            yield from _bodies(value)


def _strings(node: Any, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, key)
    elif isinstance(node, str) and "{{" not in node:
        yield key, node
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield key, str(node)


def _dictionary() -> set[str]:
    words: set[str] = set()
    for path in DICTIONARIES:
        if path.exists():
            # Lower-case entries only: a capitalised entry is itself a proper noun.
            words |= {w for w in path.read_text(errors="ignore").split() if w.islower()}
            break
    return words


def _date_forms(year: int, month: int, day: int) -> list[str]:
    name = MONTHS[month - 1]
    return [
        f"{year:04d}-{month:02d}-{day:02d}",
        f"{name} {day}, {year}",
        f"{name[:3]} {day}, {year}",
        f"{month:02d}/{day:02d}/{year}",
        f"{month}/{day}/{year}",
    ]


def _amount_forms(amount: float) -> list[str]:
    whole = int(amount)
    forms = [f"{whole:,}", str(whole), f"{whole:_}"]
    if whole % 100 == 0 and whole >= 1000:
        forms.append(f"{whole / 1000:g}k")
    return forms


def _distinctive_amount(amount: float) -> bool:
    return amount >= 1000 and len(str(int(amount)).rstrip("0")) >= 3


def repo_dates() -> set[str]:
    """Days this repository was worked on: they appear in docs for honest reasons."""
    out = subprocess.run(
        ["git", "log", "--all", "--format=%ad", "--date=short"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.split()
    return set(out) | {date.today().isoformat()}


def words_of(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9$][A-Za-z0-9$,.'-]*", text)]


SHINGLE = 6

# Clio's own bookkeeping, present in synced payloads: not case facts.
SYSTEM_KEYS = {"etag", "created_at", "updated_at", "type", "status", "field_type", "content_type", "uuid", "filename"}


def shingles_of(text: str) -> set[str]:
    words = words_of(text)
    return {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def synced_database() -> Path | None:
    """The app's own SQLite cache of the matter, present once someone has synced from Clio."""
    data_dir = Path(os.environ.get("SWANS_DATA_DIR") or "data")
    path = (data_dir if data_dir.is_absolute() else ROOT / data_dir) / "swans.db"
    return path if path.exists() else None


# Clio object type, as the sync stores it -> the section name the export uses.
SECTION_OF_KIND = {
    "custom_field": "custom_fields", "folder": "folders", "note": "notes", "communication": "communications",
    "task": "tasks", "calendar_entry": "calendar_entries", "expense": "expenses", "document": "documents",
    "relationship": "relationships", "matter": "matter",
}


def _from_export(path: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]], str]:
    """(section, body) pairs, contact bodies, and the client's surname, from the reference export."""
    export = json.loads(path.read_text())
    items = (export.get("contacts") or {}).get("items") or []
    client = next((item.get("body") or {} for item in items if item.get("ref") == "client"), {})
    bodies = [
        (section, body)
        for section, payload in export.items()
        if section not in ("about", "contacts")
        for body in _bodies(payload)
    ]
    return bodies, [item.get("body") or {} for item in items], client.get("last_name") or ""


def _not_uploaded(conn: sqlite3.Connection) -> str:
    """Leave out cases made from an uploaded folder: the rule is about the matter read from the source system.

    A build that keeps several cases stores uploaded ones beside the synced matter. A test upload holds
    made-up values (the same ones the fixtures use), and counting them as case facts flags the fixtures.
    """
    try:
        conn.execute("SELECT id, source FROM cases LIMIT 1")
    except sqlite3.Error:
        return ""
    return " AND matter_id NOT IN (SELECT id FROM cases WHERE source = 'upload')"


def _from_database(path: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]], str]:
    """The same three things from what the app synced: the live matter can hold more than the export."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT kind, payload FROM clio_items WHERE removed_at IS NULL" + _not_uploaded(conn)
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return [], [], ""
    bodies, contacts, client_ids = [], [], set()
    for kind, payload in rows:
        body = json.loads(payload)
        if kind == "contact":
            contacts.append(body)
        elif kind in SECTION_OF_KIND:
            bodies.append((SECTION_OF_KIND[kind], body))
            if kind == "matter" and (body.get("client") or {}).get("id") is not None:
                client_ids.add(body["client"]["id"])
    label = next((c.get("last_name") or "" for c in contacts if c.get("id") in client_ids), "")
    return bodies, contacts, label


def load() -> Terms | None:
    sources = []
    if (path := reference_file()) is not None:
        sources.append(_from_export(path))
    if (path := synced_database()) is not None:
        sources.append(_from_database(path))
    all_bodies = [pair for bodies, _, _ in sources for pair in bodies]
    contacts = [contact for _, found, _ in sources for contact in found]
    if not all_bodies and not contacts:
        return None
    terms = Terms()
    terms.matter_label = next((label for _, _, label in sources if label), "")
    dictionary = _dictionary()
    own_days = repo_dates()
    free_text: list[str] = []
    titles: list[str] = []
    contact_words: set[str] = set()

    # People and organisations.
    for body in contacts:
        first, last = (body.get("first_name") or "").strip(), (body.get("last_name") or "").strip()
        if first and last:
            terms.add(terms.hard, f"{first} {last}", "person name")
            terms.add(terms.hard, f"{last}, {first}", "person name")
            if len(last) >= 4 and last.lower() not in dictionary:
                terms.add(terms.hard, last, "surname")
            contact_words |= {first.lower(), last.lower()}
        for key in ("name", "company"):
            org = body.get(key)
            org = (org.get("name") if isinstance(org, dict) else org) or ""
            if org.strip():
                terms.add(terms.hard, org, "organisation name", 6)
                terms.add(terms.hard, ENTITY_SUFFIX.sub("", org.strip()).strip(" ,"), "organisation name", 6)
        if body.get("date_of_birth"):
            for form in _date_forms(*map(int, str(body["date_of_birth"])[:10].split("-"))):
                terms.add(terms.hard, form, "date of birth", 6)
        for key in ("primary_email_address", "primary_phone_number"):
            terms.add(terms.hard, body.get(key), "contact detail", 7)
        for group, key in (("email_addresses", "address"), ("phone_numbers", "number")):
            for entry in body.get(group) or []:
                terms.add(terms.hard, entry.get(key), "contact detail", 7)
        for address in body.get("addresses") or []:
            terms.add(terms.hard, address.get("street"), "street address", 8)
            terms.add(terms.hard, address.get("postal_code"), "postal code", 5)

    # Everything else: titles, free text, amounts, dates, identifiers.
    title_keys = {"subject", "summary", "name", "description"}
    text_keys = {"detail", "body", "description", "note", "value", "subject", "summary", "name", "location"}
    for section, body in all_bodies:
        schema_only = section in ("custom_fields", "folders", "matter_stages")
        for key, value in _strings(body):
            if key in SYSTEM_KEYS:
                continue
            if schema_only or key == "field_name":
                # Names the firm gave its own fields and folders: generic by
                # nature, but code keyed on them is worth seeing.
                if key in ("name", "field_name"):
                    terms.add(terms.warn, value, "field or folder name", 8)
                continue
            if key in title_keys and key != "description" and len(value.split()) >= 3:
                titles.append(value)
                tier = terms.code if section == "documents" else terms.hard
                terms.add(tier, value, f"{section} title", 12)
            if section == "documents" and key == "name":
                # File names have no spaces, so the title rule above misses them.
                stem = value.rsplit(".", 1)[0]
                terms.add(terms.code, value, "document name", 8)
                terms.add(terms.code, stem, "document name", 8)
                terms.add(terms.code, "__".join(stem.split("__")[:2]), "document name", 8)
            if section == "matter" and key == "description":
                terms.add(terms.hard, value, "matter description", 12)
            is_field_value = section == "matter" and key == "value" and 6 <= len(value) <= 80
            if is_field_value and not re.fullmatch(r"[\d.,$-]+|true|false", value, re.IGNORECASE):
                terms.add(terms.code, value, "custom field value", 6)
            if key in text_keys:
                free_text.append(value)
            for match in ISO_DATE.finditer(value):
                if match.group(0) not in own_days:
                    for form in _date_forms(*map(int, match.groups())):
                        terms.add(terms.hard, form, "date in the matter", 6)
            if key in ("price", "total", "value", "quantity") and re.fullmatch(r"\d+(\.\d+)?", value):
                amount = float(value)
                if _distinctive_amount(amount):
                    for form in _amount_forms(amount):
                        terms.add(terms.hard, form, "amount")

    for text in free_text:
        for found in MONEY.findall(text):
            try:
                amount = float(found.replace(",", ""))
            except ValueError:
                continue
            tier = terms.hard if _distinctive_amount(amount) else terms.warn
            if amount >= 1000:
                for form in _amount_forms(amount):
                    terms.add(tier, form if tier is terms.hard else f"${form}", "amount")
        for found in EMAIL.findall(text):
            terms.add(terms.hard, found, "email address", 7)
        for found in PHONE.findall(text):
            terms.add(terms.hard, found, "phone number", 7)
        for found in IDENTIFIER.findall(text):
            terms.add(terms.hard, found, "identifier", 6)
        if len(text.split()) >= SHINGLE:
            terms.shingles |= shingles_of(text)

    # Proper nouns that occur in the file's prose but in no dictionary: parties,
    # witnesses, carriers and places that are not Clio contacts.
    lowercase_seen = {w for text in free_text for w in WORD.findall(text) if w.islower()}
    for text in free_text:
        for word in re.findall(r"\b[A-Z][a-z]{3,}(?:-[A-Z][a-z]+)?\b", text):
            low = word.lower()
            if low in dictionary or low in GENERIC or low in lowercase_seen or low in contact_words:
                continue
            if low.endswith("s") and (low[:-1] in dictionary or low[:-1] in GENERIC):
                continue
            terms.add(terms.code, word, "proper noun")
        # Words no dictionary has: diagnoses, drugs, procedures as this file spells them.
        for word in re.findall(r"\b[a-z]{8,}\b", text):
            if word in dictionary or word in GENERIC or word.rstrip("s") in dictionary:
                continue
            if any(word.endswith(end) and word[: -len(end)] in dictionary for end in ("ed", "ing", "ly", "es", "al")):
                continue
            terms.add(terms.warn, word, "uncommon term")

    # A prefix shared by several titles is a naming convention of this one file.
    prefixes: dict[str, int] = {}
    for title in titles:
        head = re.split(r":| - | — ", title, maxsplit=1)[0].strip()
        if head != title and len(head.split()) >= 2:
            prefixes[head] = prefixes.get(head, 0) + 1
    for head, count in prefixes.items():
        if count >= 2:
            # Two ordinary words ("records request") are everyday vocabulary; three or more are a convention.
            tier = terms.code if len(head.split()) >= 3 else terms.warn
            terms.add(tier, head, "title convention", 8)

    # The matter's public label may be written in prose docs; nowhere else.
    for tier in (terms.code, terms.warn):
        for term in list(tier):
            if term in terms.hard:
                del tier[term]
    return terms


def mask(term: str) -> str:
    """Enough to find the hit, not enough to put a client detail in a log."""
    return f"{term[:2]}{'*' * max(len(term) - 2, 1)} ({len(term)} chars)"


def pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![\w])", re.IGNORECASE)
