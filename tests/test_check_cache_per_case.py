"""The check cache is per case. Two cases can hold word-for-word the same
statements (a file imported twice); an answer stored for one names that case's
claim ids and must never be served to the other. Synthetic ledgers only.
(Written by checker; critic owns this directory.)"""

from __future__ import annotations

from server.check import engine, synthetic, tier2
from server.check.engine import MemoryStore, SqliteStore
from server.db import connect
from shared.check_contract import CheckRequest

SENTENCE = "Records from Alpha Example Clinic have not been received."


def counting():
    calls: list[str] = []

    def caller(prompt: tier2.Prompt):
        calls.append(prompt.sentence)
        return tier2.fake_caller(prompt)

    return caller, calls


def test_two_cases_with_identical_statements_do_not_share_an_answer() -> None:
    engine._breaker.report(True)
    first, _ = synthetic.build(seed=7, matter_id=101)
    second, _ = synthetic.build(seed=7, matter_id=202)
    assert first.version == second.version, "the two ledgers hold the same statements"
    store = MemoryStore()
    caller, calls = counting()
    request = CheckRequest(text=SENTENCE, complete=True)

    a = synthetic.checker(first, caller=caller, store=store).run(request).spans[0]
    again = synthetic.checker(first, caller=caller, store=store).run(request).spans[0]
    b = synthetic.checker(second, caller=caller, store=store).run(request).spans[0]

    assert not a.cached and again.cached, "the same case is served its own stored answer"
    assert not b.cached and len(calls) == 2, "the other case was asked afresh, not served the first case's answer"
    assert a.id != b.id
    assert all("/api/matters/202/" in item.source.href for item in b.evidence) and b.evidence


def test_the_key_differs_by_case_and_nothing_else() -> None:
    first, _ = synthetic.build(seed=7, matter_id=101)
    second, _ = synthetic.build(seed=7, matter_id=202)
    ctx = engine.Context()
    one, two = synthetic.checker(first), synthetic.checker(second)
    assert one._key(SENTENCE, ctx, None) != two._key(SENTENCE, ctx, None)
    assert one._key(SENTENCE, ctx, None, with_case=False) == two._key(SENTENCE, ctx, None, with_case=False)


def test_a_stored_row_is_read_only_by_its_own_case(tmp_path) -> None:
    """Rows written before the case was in the key stay usable, for the case that wrote them only."""
    path = tmp_path / "t.db"
    conn = connect(path)
    mine, theirs = SqliteStore(path, 101, conn), SqliteStore(path, 202, conn)
    mine.put("an-older-key", "v", "model", {"verdict": "supported"}, 5)
    assert mine.get_before_case_keys("an-older-key") is not None
    assert theirs.get("an-older-key") is None and theirs.get_before_case_keys("an-older-key") is None
