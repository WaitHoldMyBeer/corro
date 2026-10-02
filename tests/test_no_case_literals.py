"""Nothing about the demo matter may be a literal in this repository.

The forbidden terms are derived at run time from the gitignored reference
export (see `case_terms.py`); they are never written down here. In a clean
clone the export is absent and these tests skip, saying so.

Hits are printed masked, so this check never copies a client detail into a log.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import case_terms
import pytest

ROOT = case_terms.ROOT
SKIP_REASON = (
    "HARDCODE CHECK NOT RUN: there is nothing on this machine to derive the matter's terms from"
    " (no reference export, no synced database), so the repository could not be compared against them."
    " This is not a pass. To run it: sync a matter (README, 'Connect, read, digest, open', step 4) and run"
    " scripts/check.sh again; the terms are then derived from your own data/swans.db at run time."
)

# Prose: the matter's public label may be used here, and document or field
# names are tolerated with a warning. Everything else is code, prompt, mock or fixture.
PROSE_SUFFIXES = {".md"}
PROSE_NAMES = {".gitignore"}
SKIPPED_NAMES = {"uv.lock"}
MAX_BYTES = 2_000_000


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout


def repository_files() -> list[Path]:
    """Tracked files plus anything untracked that is not ignored (it is about to be committed)."""
    names = set(_git("ls-files").splitlines()) | set(_git("ls-files", "-o", "--exclude-standard").splitlines())
    files = []
    for name in sorted(names):
        path = ROOT / name
        if path.name in SKIPPED_NAMES or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        files.append(path)
    return files


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:  # deleted between the listing and the read: nothing left to scan
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def is_prose(path: Path) -> bool:
    return path.suffix.lower() in PROSE_SUFFIXES or path.name in PROSE_NAMES


@pytest.fixture(scope="module")
def terms() -> case_terms.Terms:
    loaded = case_terms.load()
    if loaded is None:
        pytest.skip(SKIP_REASON)
    return loaded


def scan(terms: case_terms.Terms, files: list[Path] | None = None) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    label = terms.matter_label.lower()
    compiled = (
        [(case_terms.pattern(t), t, kind, "hard") for t, kind in terms.hard.items()]
        + [(case_terms.pattern(t), t, kind, "code") for t, kind in terms.code.items()]
        + [(case_terms.pattern(t), t, kind, "warn") for t, kind in terms.warn.items()]
    )
    for path in repository_files() if files is None else files:
        text = read_text(path)
        if text is None:
            continue
        prose = is_prose(path)
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else Path(path.name)
        lowered = text.lower()
        for regex, term, kind, tier in compiled:
            # Cheap pre-filter on the first word before running the regex.
            if term.split()[0].lower() not in lowered:
                continue
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                where = f"{rel}:{line}  [{kind}]  {case_terms.mask(term)}"
                if tier == "warn":
                    warnings.append(where)
                elif prose and (tier == "code" or term.lower() == label):
                    if term.lower() != label:
                        warnings.append(where)
                else:
                    failures.append(where)
        if not prose:
            copied = case_terms.shingles_of(text) & terms.shingles
            if copied:
                failures.append(f"{rel}  [{len(copied)} run(s) of {case_terms.SHINGLE} words copied from the matter's text]")
    return failures, warnings


def test_no_case_literal_in_repository_files(terms: case_terms.Terms) -> None:
    failures, warnings = scan(terms)
    if warnings:
        print(f"\n{len(warnings)} warning(s), not fatal (generic-looking terms, or prose docs):")
        for line in sorted(set(warnings)):
            print("  WARN", line)
    assert not failures, "case literals in the repository:\n  " + "\n  ".join(sorted(set(failures)))


def test_no_case_literal_in_commit_messages(terms: case_terms.Terms) -> None:
    log = _git("log", "--all", "--format=%h%x09%B%x00")
    label = terms.matter_label.lower()
    failures = []
    for entry in log.split("\0"):
        commit, _, message = entry.strip().partition("\t")
        for term, kind in terms.hard.items():
            if term.lower() == label:
                continue
            if case_terms.pattern(term).search(message):
                failures.append(f"commit {commit}  [{kind}]  {case_terms.mask(term)}")
    assert not failures, "case literals in commit messages:\n  " + "\n  ".join(failures)


def test_term_derivation_found_something(terms: case_terms.Terms) -> None:
    """Guards the guard: an export that parses to nothing would pass everything."""
    assert len(terms.hard) >= 50, "too few terms derived; the export's shape may have changed"
    assert terms.matter_label, "could not identify the client contact in the export"
    assert terms.shingles


def test_the_check_fires_on_a_planted_literal(terms: case_terms.Terms, tmp_path: Path) -> None:
    """A canary built from the derived terms: one of each class must be caught, in code and in prose."""
    by_kind: dict[str, str] = {}
    for term, kind in terms.hard.items():
        if term.lower() != terms.matter_label.lower():
            by_kind.setdefault(kind, term)
    assert {"person name", "amount", "date in the matter"} <= set(by_kind)
    for suffix in (".py", ".md", ".js"):
        for kind, term in by_kind.items():
            planted = tmp_path / f"planted{suffix}"
            planted.write_text(f"x = 1\nprompt = 'look for {term} in the file'\n")
            failures, _ = scan(terms, [planted])
            assert failures, f"a planted {kind} was not caught in a {suffix} file"
    label_in_code = tmp_path / "label.py"
    label_in_code.write_text(f"MATTER = '{terms.matter_label}'\n")
    assert scan(terms, [label_in_code])[0], "the matter's label was not caught in code"
    label_in_prose = tmp_path / "label.md"
    label_in_prose.write_text(f"The demo runs on the {terms.matter_label} matter.\n")
    assert not scan(terms, [label_in_prose])[0], "the matter's public label must be allowed in prose docs"


def test_a_case_made_in_our_own_store_adds_nothing_to_the_terms(tmp_path: Path) -> None:
    """The rule is about the matter read from the source system. A case someone creates or uploads
    here must not turn its own text into forbidden terms, or uploading a file would fail this check."""
    path = tmp_path / "swans.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE clio_items (matter_id INTEGER, kind TEXT, clio_id TEXT, payload TEXT, removed_at TEXT)")
    conn.execute("CREATE TABLE cases (id INTEGER, source TEXT)")
    conn.execute("INSERT INTO cases VALUES (-1, 'upload')")
    rows = [
        (7, "matter", "7", {"id": 7, "client": {"id": 70}}),
        (7, "contact", "70", {"id": 70, "last_name": "Readfromsource"}),
        (7, "note", "71", {"id": 71, "subject": "kept"}),
        (-1, "contact", "-10", {"id": -10, "last_name": "Madehere"}),
        (-1, "note", "-11", {"id": -11, "subject": "left out"}),
    ]
    conn.executemany(
        "INSERT INTO clio_items VALUES (?, ?, ?, ?, NULL)",
        [(matter, kind, item, json.dumps(body)) for matter, kind, item, body in rows],
    )
    conn.commit()
    conn.close()
    bodies, contacts, label = case_terms._from_database(path)
    assert [body["id"] for _, body in bodies] == [7, 71]
    assert [contact["id"] for contact in contacts] == [70]
    assert label == "Readfromsource"
