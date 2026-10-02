"""The live checker's request and response: POST /api/matters/{matter_id}/check.

One function serves a draft, call notes and an incoming provider message. Each
sentence of the submitted text comes back as a span with a verdict, the ids of
the ledger claims it rests on, and a disclosure category.

Conventions
- `start` and `end` are offsets into `CheckRequest.text` exactly as a browser
  indexes a string (UTF-16 code units), end exclusive: `text.slice(start, end)`.
- Everything a span shows about the file (claim text, quote, page, date, the
  replacement value) is copied from the claims ledger by claim id, in code. The
  model only ever returns claim ids, a verdict and a category.
- "Not in file" means no support was found. It is never a statement that the
  sentence is false.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .contract import DisclosureCategory, Model, SourceRef


class Verdict(str, Enum):
    supported = "supported"  # a source in the file says this
    contradicted = "contradicted"  # a source in the file says otherwise
    out_of_date = "out_of_date"  # true once; a newer source supersedes it
    not_in_file = "not_in_file"  # no support found; never called false
    dont_send = "dont_send"  # discloses a never-shared category to this audience


class CheckMode(str, Enum):
    draft = "draft"  # the lawyer is writing a note, letter or message
    call = "call"  # call notes: short utterances, one request each
    incoming = "incoming"  # a message received from a provider: their statement vs the file


class AudienceKind(str, Enum):
    internal = "internal"
    provider = "provider"
    defence = "defence"
    client = "client"


class EvidenceRole(str, Enum):
    supports = "supports"  # the file says the same
    contradicts = "contradicts"  # the file says otherwise
    superseded = "superseded"  # the older source the sentence agrees with
    supersedes = "supersedes"  # the newer source that replaces it
    discloses = "discloses"  # the claim whose category makes the sentence unsendable


class CheckRequest(Model):
    text: str = Field(description="The whole editor content, one utterance, or the incoming message body.")
    mode: CheckMode = CheckMode.draft
    audience: AudienceKind = AudienceKind.internal
    audience_contact_id: int | None = Field(None, description="The provider being written to, when audience is provider.")
    author_contact_id: int | None = Field(None, description="Who wrote `text`, when mode is incoming.")
    written_on: str | None = Field(
        None,
        description="YYYY-MM-DD the text was written, when it is not today (an email already in Clio)."
        " A source recorded after that date cannot contradict it.",
    )
    source_kind: str | None = Field(None, description="When the text is itself a record in the matter: its Clio type...")
    source_clio_id: int | str | None = Field(None, description="...and id. Its own claims are then not used as evidence for it.")
    max_tier: int = Field(
        2,
        ge=1,
        le=2,
        description="1 = code only, returns in milliseconds; sentences that need the model come back `pending`."
        " 2 = also resolve pending sentences with the model. Send 1 then 2 for an instant first paint.",
    )
    complete: bool = Field(
        False,
        description="Draft mode only: treat trailing text without end punctuation as a finished sentence."
        " False leaves it to tier 1 until the sentence is ended. Call and incoming text is always complete.",
    )


class CheckEvidence(Model):
    """One ledger claim a verdict rests on, copied from the ledger by id."""

    claim_id: str
    role: EvidenceRole
    text: str = Field(description="The claim as the ledger states it.")
    date: str | None = None
    origin: str = Field(description="field | notes | correspondence | document")
    category: DisclosureCategory = DisclosureCategory.internal
    source: SourceRef = Field(description="Open `source.href` for the note, email or page; carries quote and page.")


class CheckReplacement(Model):
    """One-click fix for a contradicted or stale value: replace text[start:end] with `text`."""

    start: int
    end: int
    text: str = Field(description="The file's value, formatted in code from the ledger. Never model output.")
    claim_id: str


class CheckSpan(Model):
    id: str = Field(description="Stable for the same sentence, audience and ledger version; use as a render key.")
    start: int
    end: int
    text: str = Field(description="The sentence as submitted: request.text.slice(start, end).")
    verdict: Verdict | None = Field(None, description="Null only while `pending`.")
    pending: bool = Field(False, description="True when tier 2 has not run yet for this sentence; show 'checking'.")
    checked: bool = Field(
        False,
        description="True only when the model tier read this sentence (now, or earlier and cached). False while pending,"
        " when the tier is off or failed for this sentence, and for verdicts code reached alone. A send guard clears"
        " a sentence on this flag, never on the absence of a verdict.",
    )
    partial: bool = Field(False, description="Trailing text of a draft with no end punctuation; tier 1 only.")
    fact_verdict: Verdict | None = Field(
        None, description="For dont_send: what the file says about the sentence itself (it may be true and still unsendable)."
    )
    category: DisclosureCategory | None = Field(None, description="What the sentence discloses. Null when undetermined.")
    claim_ids: list[str] = Field(default_factory=list)
    evidence: list[CheckEvidence] = Field(default_factory=list, description="Same claims as claim_ids, resolved.")
    replacement: CheckReplacement | None = None
    message: str = Field("", description="One line for the hover card, built in code from the ledger.")
    tier: int = Field(1, description="Which tier produced the verdict: 1 code, 2 model.")
    cached: bool = False
    latency_ms: int = Field(0, description="Time this sentence took when it was first checked.")


class CheckStats(Model):
    """Running totals for the Write footer, per matter, since the server started
    (cost is the stored total for the matter, so it survives a restart)."""

    sentences_checked: int = 0
    cache_hits: int = 0
    tier1_resolved: int = 0
    tier2_calls: int = 0
    tier1_p50_ms: float | None = None
    tier1_p95_ms: float | None = None
    tier2_p50_ms: float | None = Field(None, description="Measured wall time of model calls; null until one has run.")
    tier2_p95_ms: float | None = None
    cost_usd: float = Field(0.0, description="Sum of checker model calls for this matter, from API usage.")
    cost_usd_per_check: float | None = Field(None, description="cost_usd / tier2_calls recorded for the matter.")
    model: str | None = Field(None, description="Model id tier 2 uses; null when none is configured (tier 1 only).")


class CheckResult(Model):
    """Response of POST /api/matters/{matter_id}/check."""

    matter_id: int
    mode: CheckMode
    audience: AudienceKind
    audience_contact_id: int | None = None
    author_contact_id: int | None = None
    ledger_version: str = Field(description="Changes whenever the claims ledger does; cached verdicts are keyed on it.")
    ledger_claims: int = Field(0, description="Claims in the ledger. 0 = the digest has not produced any yet.")
    spans: list[CheckSpan] = Field(default_factory=list)
    pending: int = Field(0, description="Spans still waiting for tier 2. Re-post with max_tier 2 to resolve them.")
    blocked: bool = Field(False, description="True when any span is dont_send for this audience.")
    tier2_available: bool = Field(False, description="False when no model is configured or it is failing: verdicts are tier 1 only.")
    tier2_error: str | None = Field(None, description="Why tier 2 is off when a model is configured (error type only); retried after a minute.")
    latency_ms: int = 0
    stats: CheckStats = Field(default_factory=lambda: CheckStats())  # pyright: ignore[reportCallIssue]
