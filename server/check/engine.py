"""check(text, audience) -> spans.

One code path for a draft, call notes and an incoming provider message:
split into sentences, tier 1 in code, tier 2 by the model for what code cannot
settle, the leak guard for a provider audience, then every claim id resolved
against the ledger. Tier 2 answers are cached on (sentence, context, ledger
version), so re-typing and re-opening cost nothing. The checker only reports;
it never blocks writing or sending.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import statistics
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict
from pathlib import Path
from typing import ClassVar

from shared import check_contract as k

from ..digest import llm
from . import extract, tier1, tier2
from .ledger import Ledger
from .tier1 import Context, Finding

log = logging.getLogger("check")

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS check_cache (
    key            TEXT PRIMARY KEY,   -- hash of prompt version, model, sentence, context, ledger version
    matter_id      INTEGER NOT NULL,
    ledger_version TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    result         TEXT    NOT NULL,   -- the settled finding: verdict, claim ids, category. No sentence text.
    latency_ms     INTEGER NOT NULL,
    at             TEXT    NOT NULL
);
"""

SEVERITY = {None: 0, "not_in_file": 1, "supported": 2, "out_of_date": 3, "contradicted": 4, "dont_send": 5}
FAILURE_HOLD_SECONDS = 20.0
BREAKER_FAILURES = 3
BREAKER_SECONDS = 60.0
BREAKER_SECONDS_BUSY = 15.0
DEFAULT_DEADLINE_SECONDS = 8.0
MAX_TEXT_CHARS = 60_000
MAX_MODEL_SENTENCES = 60  # per request; a longer text is checked by code beyond this
MIN_WORDS_FOR_MODEL = 3
RECOVERABLE = (ArithmeticError, LookupError, TypeError, ValueError, AttributeError, sqlite3.Error)

CATEGORY_WORDS = {
    "valuation": "Case value and figures",
    "strategy": "Case strategy",
    "other_party": "Another party's information",
    "internal": "Internal firm information",
}


# --------------------------------------------------------------------------- stores


class MemoryStore:
    """Cache and usage in memory: tests and synthetic ledgers."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[dict, int, str]] = {}
        self.calls: list[llm.Usage] = []
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[dict, int] | None:
        with self._lock:
            row = self.rows.get(key)
        return (row[0], row[1]) if row else None

    def put(self, key: str, ledger_version: str, model: str, result: dict, latency_ms: int) -> None:
        with self._lock:
            self.rows[key] = (result, latency_ms, model)

    def record(self, usage: llm.Usage, ok: bool) -> None:
        with self._lock:
            self.calls.append(usage)

    def measured(self, model: str | None) -> tuple[list[int], float, int]:
        with self._lock:
            latencies = [row[1] for row in self.rows.values() if row[2] == model]
            return latencies, sum(usage.cost_usd or 0.0 for usage in self.calls), len(self.calls)


class SqliteStore:
    """Cache in our own database, usage in `llm_calls` beside the digest's, so
    cost per case includes checking. Reads use the request's connection; writes
    open their own, because model calls finish on worker threads."""

    _ready: ClassVar[set[str]] = set()

    def __init__(self, db_path: Path, matter_id: int, conn: sqlite3.Connection) -> None:
        self.db_path, self.matter_id, self.conn = db_path, matter_id, conn
        if str(db_path) not in self._ready:
            conn.executescript(CACHE_SCHEMA)
            self._ready.add(str(db_path))

    def _connect(self) -> sqlite3.Connection:
        from ..db import connect

        return connect(self.db_path)

    def get(self, key: str) -> tuple[dict, int] | None:
        row = self.conn.execute(
            "SELECT result, latency_ms FROM check_cache WHERE key=? AND matter_id=?", (key, self.matter_id)
        ).fetchone()
        return (json.loads(row["result"]), row["latency_ms"]) if row else None

    def get_before_case_keys(self, key: str) -> tuple[dict, int] | None:
        """An answer stored before the case became part of the key. The row carries its case, so it
        is served to that case only; answers already paid for are not asked again."""
        return self.get(key)

    def put(self, key: str, ledger_version: str, model: str, result: dict, latency_ms: int) -> None:
        from ..db import now_iso

        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO check_cache (key, matter_id, ledger_version, model, result, latency_ms, at)"
                " VALUES (?,?,?,?,?,?,?)",
                (key, self.matter_id, ledger_version, model, json.dumps(result), latency_ms, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def record(self, usage: llm.Usage, ok: bool) -> None:
        from ..digest.pipeline import record_call

        conn = self._connect()
        try:
            record_call(conn, self.matter_id, None, "check", usage, ok)
        finally:
            conn.close()

    def measured(self, model: str | None) -> tuple[list[int], float, int]:
        latencies = [
            row[0]
            for row in self.conn.execute(
                "SELECT latency_ms FROM check_cache WHERE matter_id=? AND model=?", (self.matter_id, model or "")
            )
        ]
        cost, calls = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM llm_calls WHERE matter_id=? AND purpose='check'", (self.matter_id,)
        ).fetchone()
        return latencies, float(cost or 0.0), int(calls or 0)


# --------------------------------------------------------------------------- running totals


class _Counters:
    def __init__(self) -> None:
        self.sentences = 0
        self.cache_hits = 0
        self.tier1_resolved = 0
        self.tier1_ms: deque[float] = deque(maxlen=5000)


_counters: dict[int, _Counters] = {}
_counters_lock = threading.Lock()


def _counter(matter_id: int) -> _Counters:
    with _counters_lock:
        return _counters.setdefault(matter_id, _Counters())


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))], 1)


# --------------------------------------------------------------------------- the checker

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="check")
_batch_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="check-batch")  # digest-time runs do not queue ahead of typing
_in_flight: dict[str, Future] = {}
_failed_at: dict[str, float] = {}
_flight_lock = threading.Lock()


class _Breaker:
    """When the model keeps failing (no credit, an outage) the checker stops
    asking for a minute and answers at tier 1 speed, then lets one call through
    to see whether it is back. Writing is never held up by a provider that is down."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.failures = 0
        self.closed_until = 0.0
        self.probing = False
        self.reason: str | None = None

    def state(self) -> str:
        """'ok', 'probe' (one call may go, nobody waits for it) or 'off'."""
        with self._lock:
            if self.failures < BREAKER_FAILURES:
                return "ok"
            if time.monotonic() < self.closed_until or self.probing:
                return "off"
            self.probing = True
            return "probe"

    def report(self, ok: bool, reason: str | None = None) -> None:
        with self._lock:
            self.probing = False
            if ok:
                self.failures, self.reason = 0, None
                return
            self.failures += 1
            self.reason = reason
            if self.failures >= BREAKER_FAILURES:
                busy = "RateLimit" in (reason or "")  # the per-minute budget refills quickly; an outage does not
                self.closed_until = time.monotonic() + (BREAKER_SECONDS_BUSY if busy else BREAKER_SECONDS)

    @property
    def message(self) -> str | None:
        with self._lock:
            return self.reason if self.failures >= BREAKER_FAILURES else None


_breaker = _Breaker()


def choose_model(cfg, patient: bool = False) -> tuple[str | None, tier2.Caller | None]:
    """The tier 2 model: CHECK_MODEL, else the bulk digest model. No key, no tier 2.
    CHECK_EFFORT sets the reasoning effort (default low; which values a model
    accepts is the provider's rule). The label returned names both, so latency
    is never averaged across settings."""
    if os.environ.get("CHECK_FAKE_MODEL", "").strip() in ("1", "true", "yes"):
        return "fake-lexical", tier2.fake_caller
    model = os.environ.get("CHECK_MODEL", "").strip() or cfg.digest_model_bulk
    if not model or not cfg.openai_api_key:
        return None, None
    effort = os.environ.get("CHECK_EFFORT", "").strip() or "low"
    return (model if effort == "low" else f"{model}@{effort}"), tier2.model_caller(cfg, model, effort, patient)


class Checker:
    def __init__(self, ledger: Ledger, store, *, model: str | None = None, caller: tier2.Caller | None = None, deadline: float | None = DEFAULT_DEADLINE_SECONDS):
        self.ledger, self.store = ledger, store
        self.model, self.caller = (model, caller) if caller else (None, None)
        self.deadline = deadline

    # -- public ------------------------------------------------------------------

    def run(self, request: k.CheckRequest) -> k.CheckResult:
        started = time.perf_counter()
        ledger = self.ledger
        text = request.text[:MAX_TEXT_CHARS]
        own = ledger.by_source.get((request.source_kind or "", str(request.source_clio_id)), []) if request.source_kind else []
        ctx = Context(
            request.mode, request.audience, request.audience_contact_id, request.author_contact_id,
            written_on=(request.written_on or "")[:10] or None, skip=frozenset(own),
        )
        counter = _counter(ledger.matter_id)
        at16 = extract.utf16_offsets(text)

        work = []  # (sentence, partial, tier 1 finding, tier 1 ms, cache key, prompt when it goes to tier 2)
        asked = 0
        for sentence in extract.split_sentences(text):
            partial = request.mode == k.CheckMode.draft and not sentence.ended and not request.complete
            clock = time.perf_counter()
            try:
                first = tier1.check(ledger, sentence.text, ctx, partial=partial) if ledger.claims else Finding()
            except RECOVERABLE as error:  # one sentence code cannot read must not take the editor down
                log.warning("check: tier 1 failed on a sentence (%s)", type(error).__name__)
                first = Finding()
            elapsed = (time.perf_counter() - clock) * 1000
            counter.tier1_ms.append(elapsed)
            prompt = None
            # A line of one or two words with no figure in it (a salutation, a signature) states
            # nothing a model could check; it is not sent.
            # For a provider every finished sentence is read by the model unless code has already
            # locked it: a verdict code reached alone says nothing about what the wording discloses.
            empty = first.verdict is None and tier1.says_nothing(ledger, sentence.text, ctx)
            if empty:
                wanted, first = False, Finding(note="nothing", decisive=True)
            elif ctx.to_provider:
                wanted = first.verdict != "dont_send" or not first.decisive
            else:
                wanted = not first.decisive and bool(
                    first.verdict is not None or first.hints or len(extract.WORD.findall(sentence.text)) >= MIN_WORDS_FOR_MODEL
                )
            if self.caller and ledger.claims and not partial and wanted and asked < MAX_MODEL_SENTENCES:
                try:
                    prompt = tier2.build_prompt(ledger, sentence.text, ctx, first.hints)
                    asked += 1
                except RECOVERABLE as error:
                    log.warning("check: could not build a tier 2 prompt (%s)", type(error).__name__)
            work.append((sentence, partial, first, elapsed, self._key(sentence.text, ctx, prompt), prompt))

        # Tier 2 for sentences code could not settle. One call per sentence, in parallel.
        second: dict[str, tuple[dict | None, int, bool, bool]] = {}  # key -> (answer, ms, cached, waiting)
        futures: dict[str, Future] = {}
        model_state = _breaker.state() if any(row[5] for row in work) else "ok"
        for sentence, _, _, _, key, prompt in work:
            if prompt is None or key in second or key in futures:
                continue
            held = self.store.get(key)
            if held is None and hasattr(self.store, "get_before_case_keys"):
                held = self.store.get_before_case_keys(self._key(sentence.text, ctx, prompt, with_case=False))
            if held is not None:
                second[key] = (held[0], held[1], True, False)
                counter.cache_hits += 1
            elif model_state != "ok":
                if model_state == "probe":
                    self._submit(key, prompt)  # one call to see whether the model is back; nobody waits for it
                    model_state = "off"
                second[key] = (None, 0, False, False)
            elif request.max_tier >= 2:
                future = self._submit(key, prompt)
                if future is not None:
                    futures[key] = future
                else:
                    second[key] = (None, 0, False, False)  # failed moments ago; not retried yet
            else:
                second[key] = (None, 0, False, True)
        give_up = None if self.deadline is None else time.monotonic() + self.deadline
        for key, future in futures.items():
            try:
                answer, ms = future.result(None if give_up is None else max(give_up - time.monotonic(), 0.0))
                second[key] = (answer, ms, False, False)
            except FutureTimeout:
                second[key] = (None, 0, False, True)  # still running; the answer lands in the cache

        spans = []
        for sentence, partial, first, elapsed, key, prompt in work:
            settled = None
            if prompt is not None:
                answer, ms, cached, waiting = second[key]
                # The stored answer is claim ids and a verdict; what they amount to is decided against the ledger as it is now.
                settled = (tier2.settle(ledger, sentence.text, answer, ctx) if answer else None, ms, cached, waiting)
            spans.append(self._span(sentence, partial, first, elapsed, key, settled, ctx, at16))
            counter.sentences += 1
            if spans[-1].tier == 1 and spans[-1].verdict and not spans[-1].pending:
                counter.tier1_resolved += 1

        return k.CheckResult(
            matter_id=ledger.matter_id,
            mode=request.mode,
            audience=request.audience,
            audience_contact_id=request.audience_contact_id,
            author_contact_id=request.author_contact_id,
            ledger_version=ledger.version,
            ledger_claims=len(ledger.claims),
            spans=spans,
            pending=sum(1 for span in spans if span.pending),
            blocked=any(span.verdict == k.Verdict.dont_send.value for span in spans),
            tier2_available=self.caller is not None and _breaker.message is None,
            tier2_error=_breaker.message if self.caller is not None else None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            stats=self.stats(),
        )

    def stats(self) -> k.CheckStats:
        counter = _counter(self.ledger.matter_id)
        latencies, cost, calls = self.store.measured(self.model)
        return k.CheckStats(
            sentences_checked=counter.sentences,
            cache_hits=counter.cache_hits,
            tier1_resolved=counter.tier1_resolved,
            tier2_calls=calls,
            tier1_p50_ms=_percentile(list(counter.tier1_ms), 0.5),
            tier1_p95_ms=_percentile(list(counter.tier1_ms), 0.95),
            tier2_p50_ms=round(statistics.median(latencies), 1) if latencies else None,
            tier2_p95_ms=_percentile([float(value) for value in latencies], 0.95),
            cost_usd=round(cost, 6),
            cost_usd_per_check=round(cost / calls, 6) if calls else None,
            model=self.model,
        )

    # -- tier 2 --------------------------------------------------------------------

    def _key(self, sentence: str, ctx: Context, prompt: tier2.Prompt | None, with_case: bool = True) -> str:
        """Normalised sentence, who it is for, and the ledger as this sentence
        sees it: the claims offered to the model when there are any, the ledger
        version otherwise. A digest still adding pages elsewhere in the file
        therefore does not throw away an answer that rested on none of them."""
        seen = [part["text"] for part in prompt.parts[:-1]] if prompt else self.ledger.version
        # The case is part of the key: two cases can hold word-for-word the same statements (a file
        # imported twice), and a stored answer names claim ids, which belong to one case's records.
        parts = [tier2.CHECK_VERSION, self.model or "", extract.normalise_sentence(sentence), ctx.mode,
                 ctx.audience, ctx.audience_contact_id, ctx.author_contact_id, ctx.written_on, seen]
        if with_case:
            parts.insert(1, self.ledger.matter_id)
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]

    def _submit(self, key: str, prompt: tier2.Prompt) -> Future | None:
        with _flight_lock:
            if key in _in_flight:
                return _in_flight[key]  # the same sentence is already with the model
            if time.monotonic() - _failed_at.get(key, -1e9) < FAILURE_HOLD_SECONDS:
                return None
            future = (_pool if self.deadline is not None else _batch_pool).submit(self._ask, key, prompt)
            _in_flight[key] = future
        future.add_done_callback(lambda _: self._landed(key))
        return future

    @staticmethod
    def _landed(key: str) -> None:
        with _flight_lock:
            _in_flight.pop(key, None)

    def _ask(self, key: str, prompt: tier2.Prompt) -> tuple[dict | None, int]:
        """Runs on a worker thread. Returns (the model's answer as claim ids, wall time in ms); None on failure."""
        assert self.caller is not None and self.model is not None
        clock = time.perf_counter()
        try:
            out, usage = self.caller(prompt)
        except llm.LLMUsageError as error:
            billed = error.usage
            self._store(lambda: self.store.record(billed, False))
            return self._failed(key, error)
        except llm.LLMError as error:
            return self._failed(key, error)
        ms = round((time.perf_counter() - clock) * 1000)
        _breaker.report(True)
        answer = tier2.answer_ids(prompt, out)
        self._store(lambda: (self.store.record(usage, True), self.store.put(key, self.ledger.version, self.model or "", answer, ms)))
        log.info("check: tier 2 answered %s in %d ms, %d claims offered", answer["verdict"], ms, len(prompt.aliases))
        return answer, ms

    @staticmethod
    def _store(write) -> None:
        try:
            write()
        except sqlite3.Error as error:
            log.warning("check: could not store a result (%s)", type(error).__name__)

    @staticmethod
    def _failed(key: str, error: Exception) -> tuple[None, int]:
        with _flight_lock:
            _failed_at[key] = time.monotonic()
        _breaker.report(False, str(error))
        log.warning("check: tier 2 failed (%s)", error)  # LLMError text carries no input
        return None, 0

    # -- assembling a span -----------------------------------------------------------

    def _span(self, sentence, partial: bool, first: Finding, elapsed: float, key: str, answer, ctx: Context, at16) -> k.CheckSpan:
        ledger = self.ledger
        final, tier, cached, pending, ms = first, 1, False, False, round(elapsed)
        note = first.note
        answered = note == "nothing"  # code read the whole line: a name or a courtesy, nothing to assess
        if answer is not None:
            second, second_ms, cached, pending = answer
            if second is not None:
                final, raised = self._merge(first, second)
                tier, ms, note, answered = (2 if raised else 1), round(elapsed) + second_ms, final.note, True
            elif not pending:
                note = note or "unchecked"

        verdict, fact_verdict, category = final.verdict, final.fact_verdict, final.category
        evidence = list(final.evidence)
        replacement = final.replacement

        # Leak guard: the same pass, in code. The provider view is an allowlist, and so is this:
        # a sentence whose every source is something no provider is shown is locked, whatever
        # its wording; so is one the model places in a never-shared category.
        if ctx.to_provider and verdict != "dont_send":
            provider = ctx.audience_contact_id
            rests_on = [(*ledger.standing(ledger.claims[claim_id], provider), claim_id) for claim_id, _ in evidence]
            never = [(category_seen, claim_id) for category_seen, standing, claim_id in rests_on if standing == "never"]
            blocked = None
            if never and not any(standing == "open" for _, standing, _ in rests_on):
                blocked = never[0]
            # Mixed sources take the most restrictive (docs/DISCLOSURE.md, rule 2) where code itself
            # tied a source to another provider and the model says the sentence is about a third party.
            others = [(category_seen, claim_id) for category_seen, _, claim_id in rests_on if category_seen == "other_party"]
            if blocked is None and others and final.third_party:
                blocked = others[0]
            # What passed between the client and the firm is privileged whatever the file holds.
            if blocked is None and answered and final.client_confidence:
                blocked = ("internal", None)
            if replacement and ledger.standing(ledger.claims[replacement["claim_id"]], provider)[1] == "never":
                replacement = None  # the file's value is itself firm-only: never offered into a provider draft
            # The model's category alone locks only where code agrees (see _corroborated);
            # otherwise it is reported on the span and the sentence is not locked.
            # A plan or an opinion states no fact a file could confirm and still discloses: the lock
            # does not depend on it. The model's category alone locks only where it is one of the two
            # it places reliably, or where a second signal agrees (see _corroborated); a catch-all
            # category nothing backs is dropped rather than reported, so no caller holds on noise.
            if blocked is None and answered and note != "nothing" and tier1.never_shared(category):
                own = any(standing == "open" for _, standing, _ in rests_on)  # it rests on something this provider may see
                if category in ("other_party", "internal") and own:
                    category = None
                elif final.third_party and category in ("other_party", "internal"):
                    blocked = ("other_party", None)
                elif self._corroborated(category, sentence.text, rests_on, ctx):
                    blocked = (category, None)
                else:
                    category = None
            if blocked:
                fact_verdict, verdict, category, note = verdict, "dont_send", blocked[0], "leak"
                replacement = None
                if blocked[1]:
                    evidence = [(blocked[1], "discloses")] + [pair for pair in evidence if pair[0] != blocked[1]]
            elif verdict is None and not pending and not partial and note not in ("no_fact", "nothing") and not answered:
                note = "unassessed"  # no figure for code to read and no model to read the wording

        if verdict is None and not pending and not partial and note not in ("no_fact", "unchecked", "unassessed", "elsewhere", "nothing"):
            has_value = bool(extract.find_amounts(sentence.text) or extract.find_dates(sentence.text))
            if has_value and (self.caller is None or not ledger.claims):
                verdict, note = "not_in_file", "none"

        resolved = [ledger.evidence(claim_id, role) for claim_id, role in evidence if claim_id in ledger.claims]
        fix = None
        if replacement and verdict in ("contradicted", "out_of_date") and replacement["claim_id"] in ledger.claims:
            fix = k.CheckReplacement(
                start=at16(sentence.start + replacement["start"]),
                end=at16(sentence.start + replacement["end"]),
                text=replacement["text"],
                claim_id=replacement["claim_id"],
            )
        # The file's value is still named in the message; a one-click replacement is for the
        # lawyer's own text, not for something a provider wrote.
        offered = None if ctx.mode == "incoming" else fix
        identity = hashlib.sha256(f"{key}:{sentence.start}".encode()).hexdigest()[:12]
        return k.CheckSpan.model_validate(
            {
                "id": identity,
                "start": at16(sentence.start),
                "end": at16(sentence.end),
                "text": sentence.text,
                "verdict": verdict,
                "pending": pending,
                "partial": partial,
                "fact_verdict": fact_verdict,
                "category": category if (verdict or pending or (answered and note != "nothing")) else None,
                "claim_ids": [item.claim_id for item in resolved],
                "evidence": resolved,
                "replacement": offered,
                "message": self._message(verdict, note, category, resolved, fix, ctx, final.detail, pending),
                "tier": tier,
                "checked": answered,
                "cached": cached,
                "latency_ms": ms,
            }
        )

    def _corroborated(self, category: str | None, sentence: str, rests_on: list[tuple[str, str, str]], ctx: Context) -> bool:
        """Whether a never-shared category the model gave the sentence is enough to lock it.
        Valuation and strategy are: they are what a provider must never read, and the model
        places them reliably. Other-party and internal are catch-alls it over-applies, so
        code must agree: for other-party, the sentence names someone other than the client
        and the reader, or rests on another party's claim; for internal, it rests on a firm
        entry filed internal and on nothing a provider may be shown."""
        if category in ("valuation", "strategy"):
            return True
        ledger = self.ledger
        if category == "other_party":
            if ledger.mentioned(sentence) - ledger.client_ids - {ctx.audience_contact_id}:
                return True
            return any(seen == "other_party" for seen, _, _ in rests_on)
        firm_only = [standing for seen, standing, _ in rests_on if seen == "internal" and standing == "never"]
        return bool(firm_only) and not any(standing == "open" for _, standing, _ in rests_on)

    @staticmethod
    def _merge(first: Finding, second: Finding) -> tuple[Finding, bool]:
        """(finding, whether tier 2 decided it). Tier 2 may raise what tier 1
        found, never lower it: a value code found in the file stays found."""
        if first.note == "leak_echo" and (second.verdict is not None or second.note == "no_fact"):
            return _finding(asdict(second)), True  # the leak guard then runs on the model's reading
        if first.note == "near" and second.note == "value_missing":
            return _finding(asdict(first)), False  # the model found the subject; code knows the figure differs
        if first.note in ("echo", "near") and second.verdict not in (None, "not_in_file"):
            # An inference from words alone yields to the model's reading when the model has one;
            # "nothing found" from the model does not erase what code matched. The replacement
            # value survives only if the model, too, holds that very claim against the sentence.
            merged = _finding(asdict(second))
            against = {claim_id for claim_id, role in merged.evidence if role == "contradicts"}
            if merged.verdict == "contradicted" and not merged.replacement and first.replacement and first.replacement["claim_id"] in against:
                merged.replacement = first.replacement
            return merged, True
        if first.verdict == "supported" and first.note == "value" and second.verdict == "contradicted" and second.note != "conflict":
            # Code found the sentence's own figure in the file, on the sentence's own subject. Other
            # figures nearby (a component of a total, a different charge) or a remark about the
            # document do not make that false; only a conflict the file's review recorded does.
            merged = _finding(asdict(first))
            merged.category, merged.third_party = second.category or first.category, second.third_party
            merged.client_confidence = second.client_confidence
            return merged, False
        if not first.verdict or SEVERITY[second.verdict] > SEVERITY[first.verdict]:
            merged = _finding(asdict(second))
            for pair in first.evidence:
                if all(pair[0] != other[0] for other in merged.evidence):
                    merged.evidence.append(pair)
            return merged, True
        merged = _finding(asdict(first))
        if second.verdict == first.verdict:
            for pair in second.evidence:
                if all(pair[0] != other[0] for other in merged.evidence):
                    merged.evidence.append((pair[0], pair[1]))
        if second.category and second.note != "no_fact":
            merged.category = second.category
        merged.third_party, merged.client_confidence = second.third_party, second.client_confidence
        return merged, False

    def _message(self, verdict, note: str, category, evidence: list[k.CheckEvidence], fix, ctx: Context, detail: str = "", pending: bool = False) -> str:
        ledger = self.ledger
        incoming = ctx.mode == "incoming"
        by_role: dict[str, k.CheckEvidence] = {}
        for item in evidence:
            by_role.setdefault(item.role, item)
        if verdict is None and note == "elsewhere":
            if pending:
                return "This figure is in the file; checking whether it is about the same subject"
            return "This figure is in the file, but not clearly for this subject"
        if verdict is None and pending:
            return "Not checked yet: the model has not answered"
        if verdict is None:
            if note == "unassessed" or (note == "unchecked" and ctx.to_provider):
                return "Not checked for disclosure: wording is only read when the model is available"
            return "Not checked: the model did not answer" if note == "unchecked" else ""
        if verdict == "supported":
            item = by_role.get("supports")
            where = ledger.cite(item.claim_id) if item else ""
            return f"Matches your file: {where}" if incoming else f"In the file: {where}"
        if verdict == "contradicted":
            item = by_role.get("contradicts")
            where = ledger.cite(item.claim_id) if item else ""
            lead = "Their statement differs from your file" if incoming else "File says"
            if fix:
                said = f"{lead}: {fix.text}" if incoming else f"{lead} {fix.text}"
                return f"{said} ({ledger.cite(fix.claim_id)})"
            if note in ("conflict", "echo"):
                start = "Their statement matches your notes, but a document in your file shows otherwise" if incoming else "Notes say this; a document in the file shows otherwise"
                return f"{start} ({where})"
            return f"{lead} otherwise ({where})" if not incoming else f"{lead} ({where})"
        if verdict == "out_of_date":
            new, old = by_role.get("supersedes"), by_role.get("superseded")
            if new and old:
                tail = f"; file now says {fix.text}" if fix else ""
                return f"Superseded {new.date or ''}: see {ledger.cite(new.claim_id)} (was {old.date or 'undated'}){tail}".replace("  ", " ")
            return "A newer source in the file supersedes this"
        if verdict == "dont_send":
            words = CATEGORY_WORDS.get(category or "", "This")
            item = by_role.get("discloses")
            where = f" ({ledger.cite(item.claim_id)})" if item else ""
            if detail:
                return f"Names another provider ({detail}): their information is never shared with a provider"
            return f"{words}: never shared with a provider{where}"
        return "Nothing in your file speaks to this" if incoming else "No source found in the file"


def _finding(row: dict) -> Finding:
    finding = Finding(**row)
    finding.evidence = [(pair[0], pair[1]) for pair in finding.evidence]
    return finding


# --------------------------------------------------------------------------- entry points


def check(cfg, conn: sqlite3.Connection, matter_id: int, request: k.CheckRequest, *, deadline: float | None = DEFAULT_DEADLINE_SECONDS) -> k.CheckResult:
    """Check `request.text` against the stored ledger of a matter."""
    from . import ledger as ledgers

    ledger = ledgers.current(cfg, conn, matter_id)
    model, caller = choose_model(cfg, patient=deadline is None)
    return Checker(ledger, SqliteStore(cfg.db_path, matter_id, conn), model=model, caller=caller, deadline=deadline).run(request)


def check_text(
    cfg,
    conn: sqlite3.Connection,
    matter_id: int,
    text: str,
    *,
    mode: str = "draft",
    audience: str = "internal",
    audience_contact_id: int | None = None,
    author_contact_id: int | None = None,
    written_on: str | None = None,
    source: tuple[str, int | str] | None = None,
    max_tier: int = 2,
    wait: bool = True,
) -> k.CheckResult:
    """The same check as a plain function, for batch use at digest (for example
    every provider communication already in Clio, with mode='incoming', the
    sender's contact id, the date it was written and source=('communication', id)
    so that it is not checked against its own claims). With `wait` it returns
    only when tier 2 has answered."""
    request = k.CheckRequest.model_validate(
        {"text": text, "mode": mode, "audience": audience, "audience_contact_id": audience_contact_id,
         "author_contact_id": author_contact_id, "max_tier": max_tier, "complete": True, "written_on": written_on,
         "source_kind": source[0] if source else None, "source_clio_id": source[1] if source else None}
    )
    return check(cfg, conn, matter_id, request, deadline=None if wait else DEFAULT_DEADLINE_SECONDS)


def stats(cfg, conn: sqlite3.Connection, matter_id: int) -> k.CheckStats:
    from . import ledger as ledgers

    model, caller = choose_model(cfg)
    return Checker(ledgers.current(cfg, conn, matter_id), SqliteStore(cfg.db_path, matter_id, conn), model=model, caller=caller).stats()
