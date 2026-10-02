"""Run-time evaluation of the checker on whatever ledger is in the local database.

Nothing about a matter is written here. The test sentences are built in memory
from the ledger the digest produced: a claim stated as the ledger states it
(expected: supported), the same sentence with its amount or date changed in
code (expected: contradicted, with the ledger's value offered as the fix), and
valuation claims addressed to a provider (expected: don't send). The report
prints group names, counts, verdicts and timings only; never a sentence.

Tier 1 always runs. The model tier runs only when CHECK_EVAL_MODEL=1 is set,
because it sends ledger text to the configured model and costs money; the
number of sentences per group is CHECK_EVAL_N (default 40).

In a clean clone there is no database and every test here skips, saying so.
"""

from __future__ import annotations

import os
import random
import sqlite3

import check_eval
import pytest

from server.check import engine, extract
from server.check import ledger as ledgers
from server.config import get_settings

SKIP = "CHECKER EVALUATION ON THE LOCAL LEDGER NOT RUN: {why}. Nothing was measured; it runs once a matter has been synced and digested."
N = int(os.environ.get("CHECK_EVAL_N", "40"))
SEED = 20


@pytest.fixture(scope="module")
def ledger():
    cfg = get_settings()
    if not cfg.db_path.exists():
        pytest.skip(SKIP.format(why="no local database (expected in a clean clone)"))
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT matter_id FROM clio_items WHERE kind='matter' AND removed_at IS NULL LIMIT 1").fetchone()
        if row is None:
            pytest.skip(SKIP.format(why="no matter has been synced"))
        built = ledgers.load(cfg, conn, int(row["matter_id"]))
    except sqlite3.Error as error:
        pytest.skip(SKIP.format(why=f"the database could not be read ({type(error).__name__})"))
    finally:
        conn.close()
    if not built.claims:
        pytest.skip(SKIP.format(why="the digest has produced no claims yet"))
    return built


def one_amount(claim):
    found = extract.find_amounts(claim.statement)
    return found[0] if len(found) == 1 and claim.amount is not None and found[0].cents == claim.amount else None


def one_date(claim):
    found = extract.find_dates(claim.statement)
    return found[0] if len(found) == 1 and claim.date and found[0].iso == claim.date else None


def settled(ledger, claim) -> bool:
    """A claim the ledger does not itself dispute: stating it should come back supported."""
    disputed = set(ledger.shown_otherwise) | {doc for docs in ledger.shown_otherwise.values() for doc in docs}
    return claim.id not in disputed


def build_cases(ledger) -> list[check_eval.Case]:
    rng = random.Random(SEED)
    # One sentence in, one verdict out: a statement the checker itself reads as two sentences
    # (an abbreviation, a list) is left out rather than scored as two.
    claims = [
        claim for claim in ledger.claims.values()
        if settled(ledger, claim) and len(claim.statement) < 400 and len(list(extract.split_sentences(claim.statement))) == 1
    ]
    rng.shuffle(claims)
    with_amount = [(claim, mention) for claim in claims if (mention := one_amount(claim)) and not extract.find_dates(claim.statement)]
    with_date = [(claim, mention) for claim in claims if (mention := one_date(claim)) and not extract.find_amounts(claim.statement)]
    providers = sorted(ledger.provider_ids)
    cases: list[check_eval.Case] = []

    for claim, mention in with_amount[:N]:
        cases.append(check_eval.Case("supported: amount as the ledger states it", claim.statement, "supported", ref=claim.id))
        changed = claim.amount + 13_700
        while changed in ledger.by_amount:
            changed += 13_700
        text = claim.statement[: mention.start] + extract.format_usd(changed, mention.wrote_cents) + claim.statement[mention.end :]
        cases.append(
            check_eval.Case(
                "contradicted: amount changed in code", text, "contradicted",
                replacement_ok=lambda fix, cents=claim.amount: bool(fix) and [m.cents for m in extract.find_amounts(fix)] == [cents],
                ref=claim.id,
            )
        )
    for claim, mention in with_date[:N]:
        cases.append(check_eval.Case("supported: date as the ledger states it", claim.statement, "supported", ref=claim.id))
        changed, days = claim.date, 0
        while changed in ledger.by_date:
            days += 9
            changed = check_eval.shift(claim.date, days)
        text = claim.statement[: mention.start] + extract.format_date(changed, mention) + claim.statement[mention.end :]
        cases.append(
            check_eval.Case(
                "contradicted: date changed in code", text, "contradicted",
                replacement_ok=lambda fix, iso=claim.date: bool(fix) and [m.iso for m in extract.find_dates(fix)] == [iso],
                ref=claim.id,
            )
        )
    if providers:
        valuation = [(claim, mention) for claim, mention in with_amount if claim.category == "valuation"]
        for claim, _ in valuation[:N]:
            outsiders = [p for p in providers if p not in claim.contact_ids]
            if outsiders:
                cases.append(
                    check_eval.Case("don't send: valuation claim to a provider", claim.statement, "dont_send", audience="provider", contact_id=rng.choice(outsiders), ref=claim.id)
                )
    return cases


def test_tier_1_on_the_local_ledger(ledger) -> None:
    cases = build_cases(ledger)
    if not cases:
        pytest.skip(SKIP.format(why="no claim in the ledger carries a single amount or date"))
    checker = engine.Checker(ledger, engine.MemoryStore(), caller=None, deadline=None)
    summary = check_eval.summarise(check_eval.run(checker, ledger, cases))
    print(check_eval.render(f"Checker, local ledger ({len(ledger.claims)} claims), tier 1 (code only), sentences built at run time", summary))
    assert not summary["defects"], summary["defects"]


@pytest.mark.parametrize("effort", check_eval.requested_efforts())
def test_tier_2_on_the_local_ledger(ledger, effort: str) -> None:
    """Opt-in: the same run-time sentences with the real model as tier 2, once per effort in CHECK_EVAL_EFFORTS."""
    if not check_eval.model_tier_requested():
        pytest.skip(check_eval.MODEL_TIER_SKIP + "; this set sends ledger text to the model")
    made = check_eval.model_checker(ledger, effort)
    if made is None:
        pytest.skip(SKIP.format(why="no model is configured"))
    checker, store, label = made
    outcomes = check_eval.run(checker, ledger, build_cases(ledger))
    summary = check_eval.summarise(outcomes)
    check_eval.write_misses(outcomes, label)
    print(check_eval.render(f"Checker, local ledger ({len(ledger.claims)} claims), tier 1 + model {label}, sentences built at run time", summary))
    print(check_eval.model_lines(store, label, outcomes))
    assert not summary["defects"], summary["defects"]
