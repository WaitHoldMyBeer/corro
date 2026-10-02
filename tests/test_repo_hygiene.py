"""What must never be tracked: case materials, secrets, local data, the mockup.

Checks the index, the working tree's about-to-be-added files, and history
(a file committed and later removed is still published by a push).
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns, not names: nothing here identifies the matter.
FORBIDDEN = (
    "*Case Materials*",
    "*-clio-data.json",
    "HANDOFF.md",
    "mockup/*",
    "data/*",
    ".env",
    ".env.*",
    "*.db",
    "*.db-*",
    "*.sqlite*",
    "*.zip",
    "*.pdf",
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.heic",
    "*.tif",
    "*.tiff",
    "*.docx",
    "*.eml",
    "*.msg",
    ".conduct/*",
    "*.pem",
    "*.key",
)
ALLOWED = {".env.example"}

# Paths that must be ignored when they exist on this machine.
MUST_BE_IGNORED = ("HANDOFF.md", "mockup", "data", ".env", ".venv")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout
    return [line for line in out.splitlines() if line.strip()]


def forbidden(paths: list[str]) -> list[str]:
    hits = []
    for path in paths:
        if path in ALLOWED:
            continue
        if any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat) for pat in FORBIDDEN):
            hits.append(path)
    return sorted(set(hits))


def test_nothing_forbidden_is_tracked() -> None:
    assert not forbidden(git("ls-files")), "tracked files that policy says stay out of the repository"


def test_nothing_forbidden_is_waiting_to_be_added() -> None:
    """Untracked and not ignored: one `git add` away from a commit."""
    assert not forbidden(git("ls-files", "-o", "--exclude-standard")), "unignored files that must be ignored"


def test_nothing_forbidden_was_ever_committed() -> None:
    history = git("log", "--all", "--diff-filter=A", "--name-only", "--format=")
    assert not forbidden(history), "paths in history that a push would publish; history needs rewriting before any push"


def test_local_only_paths_are_ignored() -> None:
    present = [path for path in MUST_BE_IGNORED if (ROOT / path).exists()]
    not_ignored = [
        path
        for path in present
        if subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False).returncode != 0
    ]
    assert not not_ignored, f"present but not ignored: {not_ignored}"


def test_no_large_or_binary_file_is_tracked() -> None:
    offenders = []
    for name in git("ls-files"):
        path = ROOT / name
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > 1_000_000:
            offenders.append(f"{name} ({size} bytes)")
        elif b"\0" in path.read_bytes()[:4096]:
            offenders.append(f"{name} (binary)")
    assert not offenders, f"large or binary tracked files: {offenders}"


def test_env_example_holds_no_value_that_looks_like_a_secret() -> None:
    example = ROOT / ".env.example"
    if not example.exists():
        return
    leaked = []
    for line in example.read_text().splitlines():
        key, sep, value = line.partition("=")
        if not sep or line.lstrip().startswith("#"):
            continue
        if any(word in key.upper() for word in ("SECRET", "KEY", "TOKEN", "PASSWORD")) and value.strip():
            leaked.append(key.strip())
    assert not leaked, f".env.example carries values for: {leaked}"
