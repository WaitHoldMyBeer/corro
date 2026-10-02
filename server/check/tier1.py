"""Tier 1: what code alone can say about a sentence, in milliseconds.

Money amounts and dates written in the sentence are looked up in the ledger.

- The same value on the same subject      -> supported, with the claim.
- That claim is one a document in the file
  disagrees with (a stored conflict)      -> contradicted, with the document.
- A different value where the file holds
  exactly one value for that subject      -> contradicted, the file's value offered.
- For a provider: a figure the file holds
  as valuation, or as another provider's  -> dont_send.

When the subject is ambiguous (several values could be meant) tier 1 says
nothing and leaves the sentence to tier 2. It never guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import extract
from .ledger import NEVER_SHARED, PROVIDER_OWN, Ledger, LedgerClaim

SECOND_PERSON = re.compile(r"\b(you|your|yours)\b", re.IGNORECASE)
FIRST_PERSON = re.compile(r"\b(we|our|ours|us|my|i)\b", re.IGNORECASE)

MAX_EVIDENCE = 3
ECHO_SHARE_OF_SENTENCE = 0.6
ECHO_SHARE_OF_ENTRY = 0.4
SUPPORT_SHARE = 0.5  # share of the sentence's subject words a claim with the same figure must carry to be support
NEAR_MISS_SHARE = 0.6  # share of the sentence's subject words a claim must carry to count as the same subject


@dataclass
class Finding:
    """What one tier concluded about a sentence. Offsets are relative to the sentence."""

    verdict: str | None = None
    fact_verdict: str | None = None
    category: str | None = None
    evidence: list[tuple[str, str]] = field(default_factory=list)  # (claim id, role)
    replacement: dict | None = None  # {start, end, text, claim_id}
    decisive: bool = False  # tier 2 has nothing to add
    hints: list[str] = field(default_factory=list)  # claim ids worth showing the model
    note: str = ""  # which rule fired; selects the hover line
    detail: str = ""  # a name from the ledger for the hover line
    third_party: bool = False  # tier 2: the sentence is about someone other than writer, reader and client
    client_confidence: bool = False  # tier 2: the sentence reports what passed between the client and the firm

    def add(self, claim_id: str, role: str) -> None:
        if (claim_id, role) not in self.evidence:
            self.evidence.append((claim_id, role))


@dataclass
class Context:
    mode: str = "draft"
    audience: str = "internal"
    audience_contact_id: int | None = None
    author_contact_id: int | None = None
    written_on: str | None = None  # YYYY-MM-DD; None = today
    skip: frozenset[str] = frozenset()  # claims drawn from the text itself: no evidence for it

    @property
    def to_provider(self) -> bool:
        return self.audience == "provider" and self.mode != "incoming"

    def usable(self, claim: LedgerClaim) -> bool:
        return claim.id not in self.skip

    def can_contradict(self, claim: LedgerClaim) -> bool:
        """A source recorded after the text was written may supersede it, not contradict it."""
        if claim.id in self.skip:
            return False
        if not self.written_on:
            return True
        # Neither can a claim about something dated after the text was written.
        return not ((claim.record_date or "")[:10] > self.written_on or (claim.date or "") > self.written_on)


@dataclass
class _Value:
    kind: str  # amount | date
    mention: Any
    key: Any  # cents or ISO date
    state: str = "open"  # supported | contradicted | open
    support: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)
    replacement: dict | None = None
    note: str = ""
    elsewhere: list[str] = field(default_factory=list)  # claims carrying the value for another subject


def _subject(ledger: Ledger, sentence: str, ctx: Context) -> tuple[set[int], int | None]:
    """(contacts the sentence names, the contact it implies by 'your' or 'our')."""
    named = ledger.mentioned(sentence) - ledger.client_ids
    implied = None
    if ctx.mode == "incoming":
        if ctx.author_contact_id and FIRST_PERSON.search(sentence):
            implied = ctx.author_contact_id
    elif ctx.audience_contact_id and SECOND_PERSON.search(sentence):
        implied = ctx.audience_contact_id
    return named, implied


def _rank(ledger: Ledger, tokens: set[str], claims: list[LedgerClaim], who: set[int]) -> list[tuple[LedgerClaim, int, float, bool]]:
    scored = []
    for claim in claims:
        shared, share = ledger.overlap(tokens, claim)
        scored.append((claim, shared, share, bool(who & claim.contact_ids)))
    scored.sort(key=lambda row: (row[3], row[2], row[1], row[0].origin == "document", row[0].when or ""), reverse=True)
    return scored


def _values_of(claim: LedgerClaim, kind: str) -> set:
    if kind == "amount":
        if claim.amount is not None:
            return {claim.amount}
        return set(claim.amounts) if len(claim.amounts) == 1 else set()
    if claim.date:
        return {claim.date}
    return set(claim.dates) if len(claim.dates) == 1 else set()


def _format(kind: str, value, mention) -> str:
    if kind == "amount":
        return extract.format_usd(value, mention.wrote_cents)
    return extract.format_date(value, mention)


def _replacement(kind: str, value, mention, claim_id: str) -> dict:
    return {"start": mention.start, "end": mention.end, "text": _format(kind, value, mention), "claim_id": claim_id}


def _check_value(ledger: Ledger, value: _Value, tokens: set[str], named: set[int], implied: int | None, ctx: Context) -> None:
    who = named or ({implied} if implied else set())
    index = ledger.by_amount if value.kind == "amount" else ledger.by_date
    exact = [ledger.claims[claim_id] for claim_id in index.get(value.key, []) if claim_id not in ctx.skip]

    if exact:
        # The same figure is support only where it is the same subject: the party the sentence
        # names, or most of the sentence's own subject words. A figure the file holds for
        # something else (another party's limit, another benefit) is not support.
        ranked = _rank(ledger, tokens, exact, who)
        if not tokens and not who:
            related = ranked  # the sentence is little more than the value itself
        elif implied is not None and not named:
            related = [row for row in ranked if row[3]]  # "your" or "our" figure: only that party's own
        else:
            related = [row for row in ranked if row[3] or (row[1] >= 1 and row[2] >= SUPPORT_SHARE)]
            if any(row[3] for row in related):
                related = [row for row in related if row[3]]  # the party the sentence is about, not others with the same figure
        if related:
            related = [row for row in related if row[2] >= 0.5 * related[0][2]]
            value.support = [row[0].id for row in related[:MAX_EVIDENCE]]
            value.state = "supported"
            # A firm entry that a document in the file disagrees with.
            for claim_id in value.support:
                documents = [i for i in ledger.shown_otherwise.get(claim_id, []) if ctx.can_contradict(ledger.claims[i])]
                if documents:
                    value.state, value.note = "contradicted", "conflict"
                    value.against = list(dict.fromkeys(documents))[:MAX_EVIDENCE]
                    theirs = set()
                    for document_id in value.against:
                        theirs |= _values_of(ledger.claims[document_id], value.kind)
                    theirs.discard(value.key)
                    if len(theirs) == 1:
                        carrier = next(i for i in value.against if theirs & _values_of(ledger.claims[i], value.kind))
                        value.replacement = _replacement(value.kind, next(iter(theirs)), value.mention, carrier)
                    break
            return
        value.elsewhere = [row[0].id for row in ranked[:MAX_EVIDENCE]]  # in the file, for a different subject

    # No claim carries this value for this subject. Is there one subject it must be about?
    if (not tokens and not who) or getattr(value.mention, "bare", False):
        return  # a plain number may not be money at all: it is looked up, never corrected
    pool = [claim for claim in ledger.claims.values() if _values_of(claim, value.kind) and ctx.can_contradict(claim)]
    need = 2 if value.kind == "date" else 1  # a date is tied to its event; one shared word is not enough
    ranked = [row for row in _rank(ledger, tokens, pool, who) if row[1] >= need and row[2] >= NEAR_MISS_SHARE]
    if implied is not None and not named:
        ranked = [row for row in ranked if row[3]]  # "your" figure is compared with that party's own, nothing else
    elif who and any(row[3] for row in ranked):
        ranked = [row for row in ranked if row[3]]
    if not ranked:
        return
    best = max(row[2] for row in ranked)
    group = [row for row in ranked if row[2] >= 0.8 * best]
    claims = [row[0] for row in group]
    # Where the file disagrees with itself on this subject, the document side is what the file shows.
    documents_over_entries = {
        document_id
        for claim in claims
        for document_id in ledger.shown_otherwise.get(claim.id, [])
        if ctx.can_contradict(ledger.claims[document_id])
    }
    if documents_over_entries:
        claims = [ledger.claims[claim_id] for claim_id in documents_over_entries]
    values = set()
    for claim in claims:
        values |= _values_of(claim, value.kind)
    if len(values) != 1:
        value.support = [claim.id for claim in claims[:MAX_EVIDENCE]]  # ambiguous: hints for tier 2
        return
    strong = (
        bool(documents_over_entries)
        or len(claims) >= 2
        or any(row[1] >= 2 or row[3] for row in group)
    )
    if not strong:
        value.support = [claim.id for claim in claims[:MAX_EVIDENCE]]
        return
    file_value = next(iter(values))
    claims.sort(key=lambda claim: (claim.origin == "document", claim.when or ""), reverse=True)
    value.state, value.note = "contradicted", "near"
    value.against = [claim.id for claim in claims[:MAX_EVIDENCE]]
    value.replacement = _replacement(value.kind, file_value, value.mention, claims[0].id)


def _leak(ledger: Ledger, values: list[_Value], ctx: Context) -> tuple[str, str] | None:
    """(category, claim id) when a figure in the sentence is one a provider must not be sent."""
    provider = ctx.audience_contact_id
    for value in values:
        if value.kind != "amount":
            continue
        exact = [ledger.claims[claim_id] for claim_id in ledger.by_amount.get(value.key, [])]
        if not exact:
            continue
        if any(ledger.category_for(claim, provider) in PROVIDER_OWN and ledger.is_own(claim, provider) for claim in exact):
            continue  # it is this provider's own figure
        for category in ("valuation", "other_party"):
            for claim in exact:
                if ledger.category_for(claim, provider) == category:
                    return category, claim.id
    return None


def check(ledger: Ledger, sentence: str, ctx: Context, *, partial: bool = False) -> Finding:
    finding = Finding()
    amounts = extract.find_amounts(sentence, bare=True)
    dates = extract.find_dates(sentence)
    if partial:
        # A value at the very end of an unfinished sentence is still being typed.
        end = len(sentence.rstrip())
        amounts = [mention for mention in amounts if mention.end < end]
        dates = [mention for mention in dates if mention.end < end]
    values = [_Value("amount", mention, mention.cents) for mention in amounts]
    values += [_Value("date", mention, mention.iso) for mention in dates]

    bare = sentence
    for mention in sorted([*amounts, *dates], key=lambda item: item.start, reverse=True):
        bare = bare[: mention.start] + " " + bare[mention.end :]
    named, implied = _subject(ledger, sentence, ctx)
    name_words = set()
    for contact_id in named:
        name_words |= extract.content_tokens(ledger.contact_by_id[contact_id].name)
    subject = extract.content_tokens(bare)
    tokens = subject - name_words
    if ctx.to_provider:
        # Someone else's information is not this provider's to see (docs/DISCLOSURE.md, rule 4):
        # a sentence that names another provider organisation, an insurer or an adverse party
        # from the matter's contacts is held. Roles and names come from Clio. Individual
        # providers are left to tier 2: Clio does not say which practice a doctor belongs to.
        others = []
        for contact_id in sorted(named - {ctx.audience_contact_id}):
            contact = ledger.contact_by_id[contact_id]
            if contact.role in ("insurer", "adverse") or (contact.role == "provider" and contact.organisation):
                others.append(contact)
        if others:
            return Finding(verdict="dont_send", category="other_party", note="leak", detail=others[0].name, decisive=True)
    if not values:
        if partial:
            return finding
        return _restates_firm_only(ledger, subject, ctx) or _echo(ledger, sentence, subject, ctx) or finding

    for value in values:
        _check_value(ledger, value, tokens, named, implied, ctx)

    contradicted = [value for value in values if value.state == "contradicted"]
    supported = [value for value in values if value.state == "supported"]
    if contradicted:
        first = contradicted[0]
        # An exact match against a stored conflict is settled. A near miss is an inference from
        # words: shown at once, and replaced by the model's reading when one is available.
        finding.verdict, finding.note, finding.decisive = "contradicted", first.note, first.note != "near"
        finding.replacement = first.replacement
        for value in contradicted:
            for claim_id in value.against:
                finding.add(claim_id, "contradicts")
            for claim_id in value.support:
                finding.add(claim_id, "supports")
    elif supported and len(supported) == len([value for value in values if value.state != "open" or not getattr(value.mention, "bare", False)]):
        finding.verdict, finding.note = "supported", "value"
        for value in supported:
            for claim_id in value.support:
                finding.add(claim_id, "supports")
    for value in values:
        finding.hints.extend(value.support + value.against + value.elsewhere)
    if finding.verdict is None and any(value.elsewhere for value in values):
        finding.note = "elsewhere"

    if finding.verdict is None and not partial:
        echo = _restates_firm_only(ledger, subject, ctx) or _echo(ledger, sentence, subject, ctx)
        if echo:
            echo.hints += finding.hints
            finding = echo

    if finding.evidence:
        lead = ledger.claims[finding.evidence[0][0]]
        finding.category = ledger.category_for(lead, ctx.audience_contact_id if ctx.to_provider else None)

    if ctx.to_provider:
        leak = _leak(ledger, values, ctx)
        if leak:
            category, claim_id = leak
            finding.fact_verdict = finding.verdict
            finding.verdict, finding.category, finding.note, finding.decisive = "dont_send", category, "leak", True
            finding.replacement = None
            finding.evidence = [(claim_id, "discloses")] + [pair for pair in finding.evidence if pair[0] != claim_id]
    return finding


def _restates_firm_only(ledger: Ledger, tokens: set[str], ctx: Context) -> Finding | None:
    """For a provider: the sentence says, in much the same words, what a firm
    entry filed as valuation or strategy says. Word overlap only, so it is shown
    at once and yields to the model's reading when one is available."""
    if not ctx.to_provider or len(tokens) < 2:
        return None
    best: tuple[float, str] | None = None
    for claim_id in ledger.firm_only_ids:
        if claim_id in ctx.skip:
            continue
        claim = ledger.claims[claim_id]
        shared = tokens & claim.tokens
        if len(shared) < 2:
            continue
        _, share = ledger.overlap(tokens, claim)
        weight = sum(ledger.idf.get(token, ledger.idf_unseen) for token in claim.tokens)
        of_claim = sum(ledger.idf[token] for token in shared) / weight if weight else 0.0
        if share >= ECHO_SHARE_OF_SENTENCE and of_claim >= ECHO_SHARE_OF_ENTRY and (best is None or share > best[0]):
            best = (share, claim_id)
    if best is None:
        return None
    claim = ledger.claims[best[1]]
    return Finding(verdict="dont_send", fact_verdict="supported", category=claim.category, note="leak_echo",
                   evidence=[(claim.id, "discloses")], hints=[claim.id])


def _echo(ledger: Ledger, sentence: str, tokens: set[str], ctx: Context) -> Finding | None:
    """The sentence repeats, in its own words, a firm entry that a document in
    the file disagrees with (a stored conflict). Matching is by words alone, so
    it needs most of the sentence's subject to be the entry's, a fair part of
    the entry to be in the sentence, and the same polarity; anything less is
    left to tier 2."""
    if len(tokens) < 2 or not ledger.shown_otherwise:
        return None
    best: tuple[float, str] | None = None
    for claim_id in ledger.shown_otherwise:
        if claim_id in ctx.skip or not any(ctx.can_contradict(ledger.claims[i]) for i in ledger.shown_otherwise[claim_id]):
            continue
        claim = ledger.claims[claim_id]
        shared = tokens & claim.tokens
        if len(shared) < 2:
            continue
        _, share = ledger.overlap(tokens, claim)
        weight = sum(ledger.idf.get(token, ledger.idf_unseen) for token in claim.tokens)
        of_claim = sum(ledger.idf[token] for token in shared) / weight if weight else 0.0
        if share < ECHO_SHARE_OF_SENTENCE or of_claim < ECHO_SHARE_OF_ENTRY:
            continue
        if extract.negated(sentence) != extract.negated(claim.statement):
            continue
        if best is None or share > best[0]:
            best = (share, claim_id)
    if best is None:
        return None
    entry = best[1]
    # Not decisive: shown at once, and when a model is available its reading of the sentence replaces this one.
    finding = Finding(verdict="contradicted", note="echo", hints=[entry])
    for document_id in [i for i in dict.fromkeys(ledger.shown_otherwise[entry]) if ctx.can_contradict(ledger.claims[i])][:MAX_EVIDENCE]:
        finding.add(document_id, "contradicts")
    finding.add(entry, "supports")
    finding.category = ledger.category_for(ledger.claims[entry], ctx.audience_contact_id if ctx.to_provider else None)
    return finding


def says_nothing(ledger: Ledger, sentence: str, ctx: Context) -> bool:
    """A line with no figure, no date and no word of substance once the client's and the
    reader's own names are taken out: a salutation, a "Re:" line, a sign-off. Code can
    see there is nothing in it to disclose or to check."""
    if extract.find_amounts(sentence, bare=True) or extract.find_dates(sentence):
        return False
    own_names: set[str] = set(ledger.firm_words)
    for contact_id in {*ledger.client_ids, ctx.audience_contact_id, ctx.author_contact_id} - {None}:
        contact = ledger.contact_by_id.get(contact_id)
        if contact:
            own_names |= extract.content_tokens(contact.name)
    return not (extract.content_tokens(sentence) - own_names)


RESTRICTIVENESS = ["internal", "strategy", "valuation", "other_party", "coverage", "attendance", "asks", "records", "bills", "status"]


def most_restrictive(categories) -> str | None:
    found = [category for category in categories if category]
    if not found:
        return None
    return min(found, key=lambda category: RESTRICTIVENESS.index(category) if category in RESTRICTIVENESS else 0)


def never_shared(category: str | None) -> bool:
    return category in NEVER_SHARED
