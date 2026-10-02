"""Spend by purpose is arithmetic over `llm_calls`. Synthetic rows only.
(Written by checker; critic owns this directory.)"""

from __future__ import annotations

from server.costs.report import group_of, report
from server.db import connect
from server.digest import llm


def priced_model(with_cache_rate: bool) -> str:
    return next(name for name, price in llm.PRICES["models"].items() if (price.get("cached_input") is not None) == with_cache_rate)


def add(conn, purpose: str, model: str, tokens_in: int, cached: int, written: int, tokens_out: int, ok: int = 1) -> None:
    cost = llm.cost_usd(model, tokens_in, cached, written, tokens_out)
    conn.execute(
        "INSERT INTO llm_calls (matter_id, run_id, purpose, model, input_tokens, cached_tokens, cache_write_tokens, output_tokens,"
        " cost_usd, seconds, ok, at) VALUES (1, NULL, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, '2031-01-01T00:00:00Z')",
        (purpose, model, tokens_in, cached, written, tokens_out, cost, ok),
    )


def test_purposes_fall_into_the_named_groups() -> None:
    assert group_of("probe") == "other", "set-up probes are not part of the cost of reading a case"
    assert [group_of(p) for p in ("pages", "reconcile", "check", "card_design", "dashboard_design", "assistant_turn", "ingest_reconcile", "negotiation_summary", "x")] == [
        "digest", "digest", "check", "card_design", "dashboard_design", "assistant", "ingestion", "negotiation", "other"]


def test_cached_reads_show_as_a_saving_and_unread_writes_as_a_cost(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    model = priced_model(True)
    add(conn, "pages", model, 100_000, 0, 100_000, 1_000)  # everything written to the cache, nothing read back
    add(conn, "check", model, 100_000, 90_000, 0, 1_000)  # most of the prompt read from the cache
    add(conn, "check", model, 1_000, 0, 0, 10, ok=0)
    conn.commit()
    got = {line.group: line for line in report(conn).lines}
    assert got["digest"].caching_effect_usd < 0, "cache writes nobody reads cost more than the plain rate"
    assert got["check"].caching_effect_usd > 0 and got["check"].failed == 1 and got["check"].calls == 2
    assert got["check"].cached_share == round(90_000 / 101_000, 3)
    total = report(conn).total
    assert total.calls == 3 and round(total.usd, 4) == round(sum(line.usd for line in got.values()), 4)
    assert report(conn, matter_id=2).total.calls == 0


def test_a_model_without_a_cached_rate_counts_no_saving(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    add(conn, "card_design", priced_model(False), 5_000, 4_900, 0, 100)
    conn.commit()
    result = report(conn)
    line = result.lines[0]
    assert line.cache_priced is False and line.caching_effect_usd == 0 and result.notes


def test_every_figure_is_per_case_when_a_case_is_given_and_the_total_is_explicit(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    model = priced_model(True)
    add(conn, "pages", model, 10_000, 0, 0, 500)  # matter 1 reads the file's pages
    conn.execute(
        "INSERT INTO llm_calls (matter_id, run_id, purpose, model, input_tokens, cached_tokens, cache_write_tokens, output_tokens,"
        " cost_usd, seconds, ok, at) VALUES (2, NULL, 'check', ?, 1000, 0, 0, 10, 0.01, 1.0, 1, '2031-01-01T00:00:00Z')", (model,)
    )
    # The same file (same bytes) is held by both cases; its three pages were read once.
    for matter in (1, 2):
        conn.execute(
            "INSERT INTO document_blobs (matter_id, document_id, version_key, sha256, size_bytes, path, downloaded_at)"
            " VALUES (?, 7, 'v', 'same-bytes', 10, 'p', '2031-01-01T00:00:00Z')", (matter,)
        )
    for page in (1, 2, 3):
        conn.execute("INSERT INTO page_reads (sha256, page, prompt_version, model, result, at) VALUES ('same-bytes', ?, 'v', ?, '{}', 'now')", (page, model))
    conn.commit()

    one, two, everything = report(conn, matter_id=1), report(conn, matter_id=2), report(conn)
    assert (one.scope, two.scope, everything.scope) == ("case", "case", "all_cases")
    assert one.total.calls == 1 and two.total.calls == 1 and everything.total.calls == 2
    assert [line.group for line in two.lines] == ["check"], "the second case paid for no page read"
    assert one.stored.pages_read == 3 and two.stored.pages_read == 3, "both cases hold the three pages"
    assert one.stored.pages_read_here == 1 and two.stored.pages_read_here == 0
    assert everything.stored.pages_read == 3 and everything.stored.pages_read_here is None
    assert [(case.matter_id, case.calls) for case in everything.by_case] == [(1, 1), (2, 1)] and not one.by_case
    assert round(sum(case.usd for case in everything.by_case), 4) == everything.total.usd
    assert any("read once per file" in note for note in two.notes)
