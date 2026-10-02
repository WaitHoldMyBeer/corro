"""Tier 2: one sentence, the claims most likely to bear on it, one model call.

The model sees claims under short aliases and returns aliases, a verdict and a
category. Nothing else it could say reaches the screen: aliases that were not
in the list are dropped here, and a verdict that its claim ids cannot carry is
lowered to "not in file". Quotes, pages, dates and replacement values are then
read from the ledger by claim id.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import model_validator

from ..digest import llm
from ..digest.schemas import Category, Strict
from . import extract
from .ledger import Ledger, LedgerClaim
from .tier1 import Context, Finding, _replacement, _values_of

CHECK_VERSION = "check-12"
HEADLINE_CLAIMS = 14
RETRIEVED_CLAIMS = 12
SAME_TOPIC_CLAIMS = 5
DISPUTED_ENTRIES = 2
MAX_CLAIMS = 34
MAX_EVIDENCE = 3
OUTPUT_TOKENS = 1000  # the answer is about fifty tokens; the provider counts this ceiling against the per-minute budget
CALL_TIMEOUT_SECONDS = 10.0  # a check that has not answered by then is reported as not checked
CALL_RETRIES = 1
BATCH_TIMEOUT_SECONDS = 45.0
BATCH_RETRIES = 5
CACHE_BREAKPOINT = {"prompt_cache_breakpoint": {"mode": "explicit"}}  # marks the end of the prompt's reusable prefix


class SentenceCheck(Strict):
    states_fact: bool
    verdict: Literal["supported", "contradicted", "out_of_date", "not_in_file"]
    agrees: list[str]
    disagrees: list[str]
    corrects: str | None
    third_party: bool
    client_confidence: bool
    category: Category

    @model_validator(mode="before")
    @classmethod
    def _older_answers(cls, data):
        """Answers written before the yes/no fields existed (stored ones, test doubles) read as false."""
        if isinstance(data, dict):
            data.setdefault("third_party", False)
            data.setdefault("client_confidence", False)
        return data


INSTRUCTIONS = """You compare one sentence with a law firm's file on one personal-injury matter. A person wrote the sentence. You do not answer it, rewrite it or advise on it; you only say how it stands against the file.

You are given who wrote the sentence and for whom, a list of claims from the file, and the sentence. Each claim has an id, an origin (field, notes and correspondence are the firm's own entries; document is a page of a document held in the file), what it says, the date it is about (date) and the date it was recorded (recorded). In the sentence, "we" and "our" mean the writer, "you" and "your" mean the reader.

Some claims carry contradicted_by: the ids of document claims that the file's own review found to disagree with that claim. If the sentence says what such a claim says, the verdict is contradicted and disagrees gives those ids.

When a date of writing is given, judge the sentence as of that date: what the sentence expected and a later claim records as done agrees with it, and a claim about something that changed afterwards does not contradict it.

Use only the claims given. Do not use outside knowledge and do not infer what the claims do not say.

Return:
- states_fact: true if the sentence asserts something about the matter that a file could confirm or contradict: an amount, a date, an event, who did what, whether something exists, was received or was done. False for greetings, thanks, questions, requests, instructions and statements of intent that assert nothing checkable.
- verdict:
  - supported: at least one claim says the same thing about the same subject.
  - contradicted: the sentence itself asserts something and a claim about the same subject says otherwise (a different amount, date, person or state of affairs), or the sentence says something is unknown, missing, not received or not done while a claim shows that it is known, held, received or done, or the reverse. Where the firm's own entries and a document disagree, the document is what the file shows. A detail the sentence does not state (a date, an amount, a name) cannot be contradicted: a claim that merely adds one is not against the sentence.
  - out_of_date: an earlier claim agrees with the sentence and a later claim on the same subject shows it has since changed.
  - not_in_file: no claim speaks to it. Use this whenever you are not sure a claim is about the same subject. It does not mean the sentence is false.
- agrees: ids of the claims that say the same as the sentence. disagrees: ids of the claims that say otherwise or that supersede it. Copy ids exactly from the list; at most three of each, the most direct first. supported needs at least one id in agrees; contradicted needs at least one in disagrees; out_of_date needs both.
- corrects: the id of the one claim in disagrees that gives the correct value of the very amount or date written in the sentence, meaning the same quantity: a balance for a balance, an offer for an offer, the date of the same event. null when the sentence has no amount or date, or when no claim gives that value (for example the claim only bounds it, or its figure is a different quantity).
- third_party: true only if the sentence tells its reader something about a person or organisation other than the writer, the reader and the client: another provider, the opposing side, an insurer, an examiner, a witness. A sentence about the client's dealings with the reader's own office, or one that only names its writer, is false.
- client_confidence: true only if the sentence reports a communication between the client and the firm: what the client told, admitted, denied or did not tell the firm. What the client did, or where the client is treated, is not a confidence.
- category: what the sentence would disclose to its reader, by the firm's disclosure rules:
  - status: where the matter stands procedurally and whether it is active: stage, open or closed, filings, hearing or trial dates, that a resolution occurred. No amounts and no reasoning.
  - bills: the reader's own charges, payments received, adjustments and outstanding balance.
  - records: the reader's own records, requests for them and receipts; what the firm holds from them and as of when.
  - attendance: dates the client attended or is booked at the reader's own appointments, without judgment.
  - asks: a specific, present request the firm makes of the reader's office.
  - coverage: only whether coverage behind the case is established, with no limits, carriers, reasoning or amounts.
  - valuation: any figure or reasoning about what the case is worth: demands, offers, reserves, limits, expected recovery, fees, liens, reduction targets.
  - strategy: counsel's impressions and plans: assessment of liability and disputes about it, weaknesses, views of witnesses, the plan for negotiation, litigation or timing, why a question is open. How the firm intends to value, settle, argue, bargain or time the case, and what it expects to obtain or concede, is strategy, with or without a figure and even when the sentence is addressed to the party concerned: the plan for negotiation, an offer that is expected or will be made, a reduction of a lien or balance that will be sought, when settlement is expected; a request made now is asks, and a promise to keep the reader informed or to send something is status.
  - other_party: health, billing, identity or contact information about anyone other than the client and the reader: other providers, the opposing side, insurers, witnesses, the client's unrelated history. Anything about another provider or another party is other_party, with or without an amount, including a comparison with the reader. Naming the client or the reader's own office is never other_party.
  - internal: anything else about the firm's own work: operations, staff, expenses, tasks, privileged communication with the client, personal details of the client a reader does not need.
  Use other_party and internal only when the sentence actually tells the reader something about a third party or about the firm's inner workings. A courtesy, an acknowledgement, or a statement about the reader's own patient, records, bills or visits belongs to status, asks, records, bills or attendance.
  If the sentence mixes categories, give the most restrictive: internal, strategy, valuation and other_party come before the rest. Give the category whatever states_fact is: a plan, an opinion or an intention asserts nothing a file could confirm, yet it discloses strategy; a courtesy, a salutation or a plain request discloses nothing beyond status or asks. A date by which something was asked for or is due is part of the ask, not strategy."""


@dataclass
class Prompt:
    sentence: str
    context_line: str
    aliases: dict[str, str]  # alias -> claim id
    claims: dict[str, LedgerClaim]  # alias -> claim
    parts: list[dict] = field(default_factory=list)


Caller = Callable[[Prompt], tuple[SentenceCheck, llm.Usage]]


def _line(alias: str, claim: LedgerClaim, contradicted_by: list[str]) -> str:
    row = {
        "id": alias,
        "origin": claim.origin,
        "kind": claim.kind,
        "topic": claim.topic,
        "says": claim.statement,
        "date": claim.date or (claim.document_date if claim.origin == "document" else None),
        "amount_usd": claim.amount / 100 if claim.amount is not None else None,
        "party": claim.party or claim.issuer,
        "recorded": (claim.record_date or "")[:10] or None,
        "contradicted_by": contradicted_by or None,
    }
    return json.dumps({key: value for key, value in row.items() if value is not None}, ensure_ascii=False)


def _who(ledger: Ledger, ctx: Context) -> str:
    def name(contact_id: int | None) -> str:
        contact = ledger.contact_by_id.get(contact_id) if contact_id else None
        return f" ({contact.name})" if contact else ""

    if ctx.mode == "incoming":
        return f"Written by: a treating provider's office{name(ctx.author_contact_id)}. For: the firm.{_dated(ctx)}"
    reader = {
        "provider": f"a treating provider's office{name(ctx.audience_contact_id)}",
        "defence": "counsel for the other side",
        "client": "the client",
    }.get(ctx.audience, "the firm's own file")
    spoken = " These are notes taken on a call." if ctx.mode == "call" else ""
    return f"Written by: the firm. For: {reader}.{spoken}{_dated(ctx)}"


def _dated(ctx: Context) -> str:
    return f" Written on: {ctx.written_on}." if ctx.written_on else ""


def build_prompt(ledger: Ledger, sentence: str, ctx: Context, hints: list[str]) -> Prompt:
    """Headline claims first and always in the same order, so the provider can
    reuse the cached prefix; then the claims retrieved for this sentence."""
    headline = [claim_id for claim_id in ledger.headline_ids if claim_id not in ctx.skip][:HEADLINE_CLAIMS]
    # The documents that stand against a headline entry travel in the same fixed block, so the
    # block is byte-identical for every sentence checked against this ledger: the provider
    # bills it at the cached rate from the second call on.
    fixed = list(headline)
    for claim_id in headline:
        for other in ledger.shown_otherwise.get(claim_id, [])[:MAX_EVIDENCE]:
            if other in ledger.claims and other not in fixed and other not in ctx.skip:
                fixed.append(other)
    chosen: list[str] = []

    def take(claim_id: str) -> None:
        if claim_id in ledger.claims and claim_id not in fixed and claim_id not in chosen and claim_id not in ctx.skip:
            chosen.append(claim_id)

    for claim_id in hints:
        take(claim_id)
    # Firm entries the stored reconciliation found a document to disagree with, when the
    # sentence is on their subject, each with those documents: the file's own disputes are
    # never left to chance retrieval.
    disputed = ledger.disputed_entries(extract.content_tokens(sentence), DISPUTED_ENTRIES)
    for claim_id in [*disputed, *hints]:
        if claim_id in disputed:
            take(claim_id)
        for other in ledger.shown_otherwise.get(claim_id, [])[:MAX_EVIDENCE]:
            take(other)
    for claim_id in hints[:4]:  # other entries on the same subject: a later one may supersede
        topic = (ledger.claims[claim_id].topic or "").strip().lower() if claim_id in ledger.claims else ""
        for other in sorted(ledger.by_topic.get(topic, []), key=lambda i: ledger.claims[i].when or "", reverse=True)[:SAME_TOPIC_CLAIMS]:
            take(other)
    # The reader's (or the author's) own recorded total, and that of any provider the sentence
    # names: "our balance", "what we billed" seldom share a word with how the file records it.
    party = ctx.author_contact_id if ctx.mode == "incoming" else ctx.audience_contact_id
    for contact_id in [party, *sorted(ledger.mentioned(sentence))]:
        take(f"provider:{contact_id}:billed_total")
    found = ledger.search(sentence, RETRIEVED_CLAIMS)
    for claim_id in found:
        take(claim_id)
        for other in ledger.shown_otherwise.get(claim_id, [])[:MAX_EVIDENCE]:
            take(other)
    chosen = chosen[: MAX_CLAIMS - len(fixed)]

    prompt = Prompt(sentence=sentence, context_line=_who(ledger, ctx), aliases={}, claims={})
    alias_of: dict[str, str] = {}
    for prefix, ids in (("h", fixed), ("r", chosen)):
        for number, claim_id in enumerate(ids, start=1):
            alias_of[claim_id] = f"{prefix}{number}"
    lines: dict[str, list[str]] = {"h": [], "r": []}
    for claim_id, alias in alias_of.items():
        prompt.aliases[alias] = claim_id
        prompt.claims[alias] = ledger.claims[claim_id]
        # A fixed line names only fixed lines, or it would change with the sentence.
        against = [alias_of[other] for other in ledger.shown_otherwise.get(claim_id, [])
                   if other in alias_of and (alias[0] == "r" or alias_of[other][0] == "h")]
        lines[alias[0]].append(_line(alias, ledger.claims[claim_id], against))
    prompt.parts = [
        # Everything up to here (instructions, answer schema, this block) is the same for every sentence.
        {**llm.text_part("Claims from the file, main facts of the matter:\n" + ("\n".join(lines["h"]) or "(none)")), **CACHE_BREAKPOINT},
        llm.text_part("Claims from the file, found for this sentence:\n" + ("\n".join(lines["r"]) or "(none)")),
        llm.text_part(f"{prompt.context_line}\nSentence:\n{sentence}"),
    ]
    return prompt


def model_caller(cfg, model: str, effort: str = "low", patient: bool = False) -> Caller:
    """`patient` is for batch runs at digest: nobody is watching a cursor, so a rate-limited
    call waits and retries instead of reporting the sentence as not checked."""

    def call(prompt: Prompt) -> tuple[SentenceCheck, llm.Usage]:
        return llm.structured(
            cfg, model=model, instructions=INSTRUCTIONS, content=prompt.parts, schema=SentenceCheck,
            effort=effort, max_output_tokens=OUTPUT_TOKENS,
            timeout=BATCH_TIMEOUT_SECONDS if patient else CALL_TIMEOUT_SECONDS,
            max_retries=BATCH_RETRIES if patient else CALL_RETRIES,
        )

    return call


# --------------------------------------------------------------------------- validation, in code


def answer_ids(prompt: Prompt, out: SentenceCheck) -> dict:
    """The model's answer with aliases turned back into claim ids. An alias that
    was not offered is dropped here. This is what the cache keeps."""
    agrees = list(dict.fromkeys(prompt.aliases[a] for a in out.agrees if a in prompt.aliases))[:MAX_EVIDENCE]
    disagrees = list(dict.fromkeys(prompt.aliases[a] for a in out.disagrees if a in prompt.aliases))[:MAX_EVIDENCE]
    disagrees = [claim_id for claim_id in disagrees if claim_id not in agrees]
    corrects = prompt.aliases.get(out.corrects or "")
    return {
        "states_fact": out.states_fact,
        "verdict": out.verdict,
        "agrees": agrees,
        "disagrees": disagrees,
        "corrects": corrects if corrects in disagrees else None,
        "third_party": out.third_party,
        "client_confidence": out.client_confidence,
        "category": out.category,
    }


def settle(ledger: Ledger, sentence: str, answer: dict, ctx: Context | None = None) -> Finding:
    """Turn the model's answer into a finding the ledger can carry."""
    ctx = ctx or Context()
    agrees = [claim_id for claim_id in answer["agrees"] if claim_id in ledger.claims and claim_id not in ctx.skip]
    disagrees = [claim_id for claim_id in answer["disagrees"] if claim_id in ledger.claims and ctx.can_contradict(ledger.claims[claim_id])]
    corrects = answer.get("corrects") if answer.get("corrects") in disagrees else None
    finding = Finding(category=answer["category"], decisive=True, third_party=bool(answer.get("third_party")),
                      client_confidence=bool(answer.get("client_confidence")))
    if not answer["states_fact"]:
        finding.note = "no_fact"
        return finding

    verdict = answer["verdict"]
    if verdict == "out_of_date":
        newest_for = max((ledger.claims[i].when or "" for i in agrees), default="")
        newest_against = max((ledger.claims[i].when or "" for i in disagrees), default="")
        if not agrees or not disagrees:
            verdict = "contradicted" if disagrees else ("supported" if agrees else "not_in_file")
        elif not (newest_for and newest_against and newest_against > newest_for):
            verdict = "contradicted"  # the dates do not show a later source; report the disagreement only
    if verdict == "contradicted" and not disagrees:
        verdict = "supported" if agrees else "not_in_file"
    if verdict == "supported" and not agrees:
        verdict = "not_in_file"

    if verdict == "supported" and not _figures_carried(ledger, sentence, agrees):
        # Numbers are checked in code: a figure or date in the sentence that none of the
        # claims said to agree actually carries is not supported by them, whatever the model says.
        verdict, agrees, finding.note = "not_in_file", [], "value_missing"

    if verdict == "supported":
        # An entry the sentence agrees with, which a document in the file disagrees with.
        for claim_id in agrees:
            documents = [i for i in ledger.shown_otherwise.get(claim_id, []) if ctx.can_contradict(ledger.claims[i])]
            if documents:
                verdict, finding.note = "contradicted", "conflict"
                disagrees = [i for i in dict.fromkeys(documents) if i not in agrees][:MAX_EVIDENCE]
                break

    fix = _one_value_fix(ledger, sentence, corrects)
    if ctx.written_on:
        # A record written in the past is judged as of its date. That later sources moved on is not
        # a finding about it, and a contradiction is kept only where code can see it: the record
        # states a figure or date and the file holds a different one for the same thing, or a
        # stored conflict stands against it. A sentence that states no date cannot be
        # contradicted by one.
        if verdict == "out_of_date":
            verdict = "supported" if agrees else "not_in_file"
        elif verdict == "contradicted" and fix is None and finding.note != "conflict":
            verdict, finding.note = ("supported" if agrees else "not_in_file"), "as_written"

    finding.verdict = verdict
    if verdict == "supported":
        finding.evidence = [(i, "supports") for i in agrees]
    elif verdict == "contradicted":
        finding.evidence = [(i, "contradicts") for i in disagrees] + [(i, "supports") for i in agrees]
        finding.replacement = fix
    elif verdict == "out_of_date":
        finding.evidence = [(i, "supersedes") for i in disagrees] + [(i, "superseded") for i in agrees]
        finding.replacement = fix
    return finding


def _figures_carried(ledger: Ledger, sentence: str, agrees: list[str]) -> bool:
    amounts: set[int] = set()
    dates: set[str] = set()
    for claim_id in agrees:
        amounts |= ledger.claims[claim_id].amounts
        dates |= ledger.claims[claim_id].dates
    return all(mention.cents in amounts for mention in extract.find_amounts(sentence)) and all(
        mention.iso in dates for mention in extract.find_dates(sentence)
    )


def _one_value_fix(ledger: Ledger, sentence: str, corrects: str | None) -> dict | None:
    """A one-click replacement, only when the model names the claim that gives the
    right value for the very quantity in the sentence, the sentence has one amount
    (or one date), and that claim carries exactly one different value of that kind.
    The model picks the claim; the value is read from the ledger."""
    if corrects is None:
        return None
    for kind, mentions in (("amount", extract.find_amounts(sentence)), ("date", extract.find_dates(sentence))):
        if len(mentions) != 1:
            continue
        mention = mentions[0]
        written = getattr(mention, "cents" if kind == "amount" else "iso")
        carried = _values_of(ledger.claims[corrects], kind) - {written}
        if len(carried) == 1:
            return _replacement(kind, next(iter(carried)), mention, corrects)
    return None


# --------------------------------------------------------------------------- stand-in for tests and for building without a key

NEGATIONS = {"not", "no", "never", "none", "without", "missing", "unknown", "denied", "unpaid", "n't"}


def fake_caller(prompt: Prompt) -> tuple[SentenceCheck, llm.Usage]:
    """A lexical stand-in with the model's interface: no network, no cost. It
    exists so the pipeline around the model (retrieval, validation, cache,
    stats) can be exercised; its verdicts are word overlap, not understanding."""
    usage = llm.Usage(model="fake-lexical", cost_usd=0.0)
    words = extract.content_tokens(prompt.sentence)
    if len(words) < 2:
        return SentenceCheck(states_fact=False, verdict="not_in_file", agrees=[], disagrees=[], corrects=None, third_party=False, client_confidence=False, category="status"), usage
    best_alias, best_share = None, 0.0
    for alias, claim in prompt.claims.items():
        shared = words & claim.tokens
        share = len(shared) / len(words)
        if len(shared) >= 2 and share > best_share:
            best_alias, best_share = alias, share
    if best_alias is None or best_share < 0.5:
        return SentenceCheck(states_fact=True, verdict="not_in_file", agrees=[], disagrees=[], corrects=None, third_party=False, client_confidence=False, category="status"), usage
    claim = prompt.claims[best_alias]
    raw = set(prompt.sentence.lower().replace("n't", " n't").split())
    negated_here = bool(raw & NEGATIONS)
    negated_there = bool(set(claim.statement.lower().replace("n't", " n't").split()) & NEGATIONS)
    if negated_here != negated_there:
        verdict, agrees, disagrees = "contradicted", [], [best_alias]
    else:
        verdict, agrees, disagrees = "supported", [best_alias], []
    answer = {"states_fact": True, "verdict": verdict, "agrees": agrees, "disagrees": disagrees, "corrects": None, "third_party": False, "client_confidence": False, "category": claim.category}
    return SentenceCheck.model_validate(answer), usage
