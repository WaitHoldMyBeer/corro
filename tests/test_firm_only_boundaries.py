"""Boundaries that keep firm-only work on the firm's side, checked on the code itself.

- What a provider can fetch is built by two modules. Neither may import the
  assistant, the negotiation plan, the review queue, the dashboard, costs or
  upload: if they cannot import them, none of that can reach a provider payload.
- The assistant reads. It has no tool that sends, shares, writes to a thread or
  changes a policy, and its package does not import the modules that do.
- Nothing the assistant, the negotiation plan or upload write goes anywhere
  but our own database: none of them may import an HTTP library or the Clio package
  (tests/test_clio_readonly.py checks that for every module).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROVIDER_SIDE = ("server/share.py", "server/threads.py")
FIRM_ONLY = ("assistant", "negotiation", "review_queue", "dashboard", "costs", "ingest", "cards", "graph", "moves", "river")
SENDING = ("share", "threads")
WRITE_WORDS = ("send", "share", "post", "message", "reply", "approve", "policy", "upload", "delete", "write", "update", "email")


def imports_of(name: str) -> set[str]:
    tree = ast.parse((ROOT / name).read_text(), name)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found |= {module.split(".")[0]} if module else set()
            found |= {part for part in module.split(".")}
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found |= set(alias.name.split("."))
    return found


def package_files(package: str) -> list[str]:
    base = ROOT / "server" / package
    if base.is_dir():
        return sorted(str(path.relative_to(ROOT)) for path in base.rglob("*.py"))
    single = ROOT / "server" / f"{package}.py"
    return [str(single.relative_to(ROOT))] if single.exists() else []


def test_what_a_provider_can_fetch_cannot_import_firm_only_work() -> None:
    offenders = [f"{name} imports {module}" for name in PROVIDER_SIDE for module in FIRM_ONLY if module in imports_of(name)]
    assert not offenders, "the provider side can reach firm-only modules:\n  " + "\n  ".join(offenders)


def test_firm_only_features_cannot_send_to_a_provider_by_themselves() -> None:
    """The assistant, the negotiation plan and the review queue may read the case; none may import
    the modules that put text in front of a provider. Anything they draft goes out, if at all,
    through the firm's send route and its guard."""
    offenders = []
    for package in ("assistant", "negotiation", "review_queue", "costs"):
        for name in package_files(package):
            reached = imports_of(name) & set(SENDING)
            if reached:
                offenders.append(f"{name} imports {sorted(reached)}")
    assert not offenders, "firm-only modules that can send:\n  " + "\n  ".join(offenders)


def test_the_assistant_has_only_reading_tools() -> None:
    from server.assistant import engine

    tools = set(engine.ARGUMENTS)
    assert tools, "the assistant's tool table was not found"
    writers = sorted(tool for tool in tools if any(word in tool.lower() for word in WRITE_WORDS))
    assert not writers, f"assistant tools that look like they change or send something: {writers}"
    assert "compose_document" in tools, "the document tool is expected to build in code, not to send"


def test_the_assistant_is_told_to_report_the_file_and_to_treat_its_text_as_data() -> None:
    from server.assistant import engine

    text = engine.INSTRUCTIONS.lower()
    for phrase in ("only what your tools return", "do not guess", "ignore them", "strategy, valuation judgement and predictions are the lawyer's"):
        assert phrase in text, f"the assistant's instructions no longer say: {phrase}"
