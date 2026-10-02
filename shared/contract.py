"""The case model: the one JSON shape the server emits and the UI renders.

Everything here is generated from what Clio returns for a matter plus the stored
digest of its documents. No field has a default that carries a case fact; every
list defaults to empty so the model is valid before the digest has run.

Conventions
- Dates are ISO 8601 strings: `YYYY-MM-DD` for date-only values, UTC
  `YYYY-MM-DDTHH:MM:SSZ` for instants.
- Money is USD as a number of dollars. Sums are computed in code, never by a model.
- Clio ids are passed through exactly as Clio returns them.
- Anything dated or AI-derived carries at least one `SourceRef` the UI can open.

Run `uv run python -m server export-schema` after editing to refresh
`shared/contract.schema.json`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.12.0"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# --------------------------------------------------------------------------- sources


class SourceKind(str, Enum):
    """Clio object types a fact can point back to."""

    matter = "matter"
    custom_field = "custom_field"
    contact = "contact"
    relationship = "relationship"
    note = "note"
    communication = "communication"
    task = "task"
    calendar_entry = "calendar_entry"
    expense = "expense"
    document = "document"


class SourceRef(Model):
    """Where a value on screen came from. Clicking it opens `href`."""

    kind: SourceKind
    clio_id: int | str = Field(
        description="Id of the Clio object, as Clio returns it: an integer, except calendar entries (string)."
        " For custom_field, the field definition id."
    )
    label: str = Field(description="Short chip text: note subject, document name, field name.")
    date: str | None = Field(None, description="The source item's own date as Clio holds it.")
    page: int | None = Field(None, description="1-based PDF page; documents only.")
    quote: str | None = Field(None, description="Verbatim excerpt supporting the value.")
    quote_verified: bool = Field(
        False, description="True when code found `quote` in the source text. Never set by the model."
    )
    href: str = Field(description="GET returns a SourceDetail for this reference.")


class SourceDetail(Model):
    """Response of GET {SourceRef.href}: the source itself, for the drawer."""

    ref: SourceRef
    title: str
    date: str | None = None
    author: str | None = None
    parties: list[str] = Field(default_factory=list, description="Senders and receivers, attendees, assignee.")
    text: str | None = Field(None, description="Body text; for a document, the text of `ref.page`.")
    highlight: str | None = Field(None, description="Substring of `text` to highlight (the verified quote).")
    page_image_href: str | None = Field(None, description="PNG render of the document page.")
    file_href: str | None = Field(None, description="The document bytes, served from our cache.")
    page_count: int | None = None


# --------------------------------------------------------------------------- vocabulary


class Derivation(str, Enum):
    clio = "clio"  # read directly from a Clio field
    computed = "computed"  # arithmetic or rule in code over Clio data
    ai = "ai"  # extracted by the model; quote checked in code


class FactStatus(str, Enum):
    confirmed = "confirmed"  # a document or a party's own statement supports it
    assumed = "assumed"  # stated by the firm, no supporting document found
    contested = "contested"  # sources disagree, see conflict_ids
    stale = "stale"  # a newer source supersedes it
    unknown = "unknown"  # nothing in the matter speaks to it


class DisclosureCategory(str, Enum):
    """Every shareable item has exactly one category. A provider's view is built
    from an allowlist of these; the last four can never be put on an allowlist."""

    status = "status"  # stage, case alive, procedural movement
    bills = "bills"  # this provider's own charges
    records = "records"  # this provider's own records, requests and receipts
    attendance = "attendance"  # whether the patient is keeping appointments with this provider
    asks = "asks"  # what the firm needs from this provider's office
    coverage = "coverage"  # a band saying whether coverage is established; never limits or amounts
    valuation = "valuation"  # firm-only
    strategy = "strategy"  # firm-only
    other_party = "other_party"  # another provider's or party's information; firm-only
    internal = "internal"  # everything else; firm-only


SHAREABLE_CATEGORIES: tuple[DisclosureCategory, ...] = (
    DisclosureCategory.status,
    DisclosureCategory.bills,
    DisclosureCategory.records,
    DisclosureCategory.attendance,
    DisclosureCategory.asks,
    DisclosureCategory.coverage,
)


class CoverageBand(str, Enum):
    """All a provider is ever told about coverage: one of three bands, no number."""

    confirmed = "confirmed"  # at least one source of payment verified in writing or by the carrier
    being_confirmed = "being_confirmed"  # the firm is still verifying
    not_established = "not_established"  # none found to date


class CoverageSignal(Model):
    shared: bool = Field(True, description="False renders as 'Not shared by the firm', so silence is not misread.")
    band: CoverageBand | None = None
    display: str
    note: str | None = Field(None, description="Fixed caveat to print under the band wherever a provider sees it.")
    sources: list[SourceRef] = Field(default_factory=list, description="Firm view only; empty in a ProviderView.")


class Fact(Model):
    """One sourced value. The header brief and key facts are all Facts."""

    id: str
    label: str
    display: str = Field(description="Formatted value ready to render.")
    amount: float | None = Field(None, description="USD, when the fact is money.")
    date: str | None = Field(None, description="When the fact is a date, or the date it was true as of.")
    detail: str | None = Field(None, description="Longer text for the deep-dive view.")
    status: FactStatus = FactStatus.unknown
    derivation: Derivation
    category: DisclosureCategory = DisclosureCategory.internal
    sources: list[SourceRef] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- matter and people


class ContactRole(str, Enum):
    client = "client"
    provider = "provider"
    adverse = "adverse"
    insurer = "insurer"
    other = "other"


class Contact(Model):
    id: int
    name: str
    type: str = Field(description="Clio contact type: Person or Company.")
    role: ContactRole = ContactRole.other
    role_text: str | None = Field(None, description="The Clio relationship description, verbatim.")
    role_derivation: Derivation = Derivation.computed
    email: str | None = None
    phone: str | None = None
    source: SourceRef


class Photo(Model):
    document_id: int | None = Field(None, description="The matter document the photo was found in.")
    page: int = 1
    image_href: str = Field(description="PNG served from our document cache.")
    source: SourceRef


class Matter(Model):
    id: int
    display_number: str | None = None
    description: str | None = None
    status: str | None = None
    practice_area: str | None = None
    open_date: str | None = None
    client: Contact | None = None
    responsible_attorney: str | None = None
    source: SourceRef


# --------------------------------------------------------------------------- header brief


class Brief(Model):
    """The 90-second header. Every member is a Fact with its source, or null
    when the matter does not hold it."""

    client_photo: Photo | None = None
    stage: Fact | None = None
    alive: Fact | None = Field(None, description="Is the case still open and moving, and the last movement.")
    case_value: Fact | None = None
    coverage: Fact | None = Field(None, description="Firm-only figure; category valuation.")
    coverage_signal: CoverageSignal | None = Field(None, description="The band a provider may be shown.")
    firm_spend: Fact | None = None
    last_client_contact: Fact | None = None
    limitations: Fact | None = None
    summary: list[Fact] = Field(default_factory=list, description="AI two-minute read, one sourced sentence each.")


# --------------------------------------------------------------------------- value graph, claims, conflicts


class NodeKind(str, Enum):
    value = "value"
    economic = "economic"
    coverage = "coverage"
    gate = "gate"
    lien = "lien"
    cost = "cost"
    fee = "fee"
    net = "net"


class ValueNode(Model):
    """One term of: net = min(case value, reachable coverage) - liens - costs - fee."""

    id: str
    kind: NodeKind
    label: str
    amount: float | None = Field(
        None, description="USD. For a gate: the amount held back, not the amount let through. Null = unknown, never 0."
    )
    status: FactStatus = FactStatus.unknown
    derivation: Derivation
    depends_on: list[str] = Field(default_factory=list, description="Node ids this amount is computed from.")
    claim_ids: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    owed_by_contact_id: int | None = Field(None, description="Contact who can settle this node, if any.")
    parent_id: str | None = Field(None, description="The node this one is a component of: a tributary of value, or a layer of coverage.")
    basis: str | None = Field(None, description="One line: what the amount rests on.")
    payer: str | None = Field(None, description="Who would pay it, for a coverage layer.")
    counted: bool = Field(True, description="False for a coverage layer that is shown but adds nothing; `basis` says why.")
    category: DisclosureCategory = DisclosureCategory.valuation


class RiverStage(Model):
    """One stage of the value river, left to right. All figures are non-negative
    USD computed in code: outflow = inflow - diverted, floored at zero. The gate
    lets through min(inflow, confirmed coverage) and diverts the rest. An unknown
    amount stays null and the stage passes its inflow through unchanged; the
    stage id is then listed in River.unknown."""

    id: str
    node_id: str | None = None
    kind: NodeKind
    label: str
    inflow: float | None = None
    outflow: float | None = None
    diverted: float | None = Field(None, description="Held back by a gate, or taken out by a lien, cost or fee.")
    status: FactStatus = FactStatus.unknown
    claim_ids: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)


class RiverCheck(Model):
    """A sum checked in code: tick when `ok`, flag when not."""

    id: str
    label: str
    ok: bool
    expected: float | None = None
    actual: float | None = None
    difference: float | None = None
    node_ids: list[str] = Field(default_factory=list)


class River(Model):
    stages: list[RiverStage] = Field(default_factory=list)
    checks: list[RiverCheck] = Field(default_factory=list)
    reading: str | None = Field(None, description="One paragraph built from a template in code; every figure is one of the river's.")
    evidence_backed_amount: float | None = Field(None, description="Components of value with a document on file behind them.")
    evidence_backed_share: float | None = Field(None, description="That amount divided by the case value, 0-1. Only as good as the statuses.")
    net: float | None = Field(None, description="Outflow of the last stage. Before fee when 'fee' is in `unknown`.")
    unknown: list[str] = Field(default_factory=list, description="Stage ids whose amount is not in the matter.")
    fee_percent: float | None = Field(None, description="Firm setting, not read from Clio. Null until set.")


class ClaimOrigin(str, Enum):
    field = "field"  # Clio custom field or matter field
    notes = "notes"  # the firm's own notes, tasks and calendar
    correspondence = "correspondence"  # emails and logged calls
    document = "document"  # a page of a file in the matter


class Claim(Model):
    """One statement the matter makes, with exactly one source."""

    id: str
    text: str
    topic: str | None = Field(None, description="Normalised subject used to line claims up against each other.")
    date: str | None = None
    date_source: str | None = Field(None, description="'clio' when the date is the Clio record's, 'document' when printed on the page.")
    origin: ClaimOrigin
    derivation: Derivation
    category: DisclosureCategory = DisclosureCategory.internal
    issuer: str | None = Field(None, description="For a document claim: whose page it is, as printed on the page.")
    source: SourceRef


class Conflict(Model):
    """The notes say one thing and a document in the file shows another."""

    id: str
    topic: str
    summary: str
    notes_claim_ids: list[str] = Field(default_factory=list)
    document_claim_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    amount_at_stake: float | None = Field(
        None, description="The amount behind the coverage gate when this subject bears on it. Computed in code."
    )
    severity: int = Field(1, ge=1, le=3)
    rank: int = Field(
        0,
        description="1 = show first. Ordered in code: by kind (answered_gap, record, expert_opinion), then the"
        " amount behind the gate, severity, a verified document side, and the number of sources.",
    )
    stale: bool = Field(False, description="A record this card rests on has changed in Clio since the card was built.")
    kind: str = Field(
        "record",
        description="answered_gap = the firm's entries call the thing unknown, missing or outstanding and a page in"
        " the file holds it; record = the entries say one thing and a record in the file says another;"
        " expert_opinion = the document side is in a Clio folder the firm keeps for expert material, so it is an"
        " opinion, not a record. The folder test is code; outstanding-versus-different is the model's reading of"
        " the entry side, validated against a two-value schema.",
    )
    kind_label: str = Field("", description="The kind in plain words, for the card.")
    notes_quotes_verified: int = Field(0, description="Entry-side claims whose quote code found in the Clio text.")
    document_quotes_verified: int = Field(0, description="Document-side claims whose quote code found in the page text.")
    source_count: int = Field(0, description="Distinct Clio records and document pages behind the two sides.")
    merged_topics: list[str] = Field(default_factory=list, description="Other topics folded into this card.")
    review: str = Field("unreviewed", description="unreviewed | confirmed | dismissed, set by the attorney.")
    reviewed_at: str | None = None
    review_href: str | None = Field(None, description="PUT {\"review\": ...} here to record the attorney's call.")


# --------------------------------------------------------------------------- what changed


class ChangeKind(str, Enum):
    new = "new"
    updated = "updated"
    removed = "removed"


class ChangeItem(Model):
    change: ChangeKind
    at: str = Field(description="When it changed in Clio (created_at / updated_at), or when our sync saw it go.")
    title: str
    summary: str | None = None
    category: DisclosureCategory = DisclosureCategory.internal
    source: SourceRef


class Changes(Model):
    since: str | None = Field(None, description="Cut-off used: the viewer's last open, or the ?since= override.")
    items: list[ChangeItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict, description="Changed items per SourceKind.")


# --------------------------------------------------------------------------- agenda


class AgendaBucket(str, Enum):
    overdue = "overdue"
    coming = "coming"
    waiting = "waiting"  # the next move belongs to someone outside the firm
    done = "done"


class AgendaItem(Model):
    id: str
    kind: SourceKind = Field(description="task or calendar_entry")
    title: str
    detail: str | None = None
    due: str | None = Field(None, description="Task due date, or calendar start.")
    end: str | None = None
    bucket: AgendaBucket
    days_from_today: int | None = Field(None, description="Negative when past. Computed in code.")
    waiting_on_contact_id: int | None = None
    waiting_on_name: str | None = None
    assignee: str | None = None
    is_limitations: bool = False
    category: DisclosureCategory = DisclosureCategory.internal
    source: SourceRef


class Agenda(Model):
    as_of: str
    overdue: list[AgendaItem] = Field(default_factory=list)
    coming: list[AgendaItem] = Field(default_factory=list)
    waiting: list[AgendaItem] = Field(default_factory=list)
    done: list[AgendaItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- timeline


class TimelineEvent(Model):
    id: str
    date: str
    date_source: str = Field("clio", description="'clio' or 'document'; see Claim.date_source.")
    label: str
    kind: str = Field("other", description="treatment | legal | communication | billing | records | other")
    importance: int = Field(1, ge=1, le=3, description="3 = one of the entries that matter.")
    contact_id: int | None = None
    claim_ids: list[str] = Field(default_factory=list)
    node_deltas: dict[str, float] = Field(default_factory=dict, description="Node id -> change in USD at this event.")
    category: DisclosureCategory = DisclosureCategory.internal
    derivation: Derivation = Derivation.clio
    sources: list[SourceRef] = Field(default_factory=list)


# --------------------------------------------------------------------------- money out


class ExpenseLine(Model):
    clio_id: int
    date: str | None = None
    amount: float
    note: str | None = None
    category_name: str | None = None
    source: SourceRef


class Spend(Model):
    total: float = 0.0
    lines: list[ExpenseLine] = Field(default_factory=list)


# --------------------------------------------------------------------------- documents


class DocumentInfo(Model):
    id: int
    name: str
    folder: str | None = None
    received_at: str | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    has_text_layer: bool | None = None
    pages_digested: int = 0
    file_href: str | None = None
    source: SourceRef


# --------------------------------------------------------------------------- providers and sharing


class Ask(Model):
    """Something the firm needs from a party outside it."""

    id: str
    contact_id: int
    text: str
    first_asked: str | None = None
    last_asked: str | None = None
    times_asked: int = 1
    due: str | None = None
    overdue: bool = False
    node_id: str | None = None
    reply: str | None = Field(None, description="Provider's answer, stored in our database, never written to Clio.")
    replied_at: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class RecordsStatus(Model):
    state: str = Field("unknown", description="unknown | requested | partial | received")
    pages: int = 0
    first_requested: str | None = None
    last_received: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class BillsStatus(Model):
    state: str = Field("unknown", description="unknown | requested | partial | received")
    billed_total: float | None = Field(None, description="Sum of this provider's charges, added in code.")
    line_count: int = 0
    last_service_date: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class Attendance(Model):
    """Appointments with this provider as recorded by the firm. States what is on
    file; never infers a missed visit, a reason or compliance from an absence."""

    signal: str = Field("unknown", description="unknown | recorded (past visits on file) | scheduled (a future one)")
    basis: str = "as recorded by the firm"
    visits: int = 0
    last_visit: str | None = None
    next_visit: str | None = None
    days_since_last_visit: int | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class SharePolicy(Model):
    """What one provider may see. Edited by the attorney before sharing."""

    contact_id: int
    allowed_categories: list[DisclosureCategory] = Field(default_factory=list)
    hidden_item_ids: list[str] = Field(default_factory=list, description="Individual items the attorney removed.")
    approved_asks: dict[str, str] = Field(
        default_factory=dict,
        description="Ask id -> the wording the attorney approved for this provider. Stored in our database."
        " An ask not listed here is not sent.",
    )
    message: str | None = Field(None, description="Cover note from the attorney.")
    updated_at: str | None = None


class ProviderIncoming(Model):
    """Response of GET .../providers/{contact_id}/incoming-checks: what this provider wrote on
    its page, read against the file by the checker. Firm side only; never part of a ProviderView."""

    checks: dict[str, dict] = Field(
        default_factory=dict,
        description="Key (message:<id> | request:<id> | reply:<ask id>) -> the CheckResult of shared/check_contract.py."
        " A missing key means not checked; a span with a null verdict means not assessed yet.",
    )
    reviews: dict[str, str] = Field(
        default_factory=dict, description="Key -> unreviewed | confirmed | dismissed. A missing key means unreviewed."
    )
    unreviewed_contradictions: int = Field(0, description="Texts with a contradicted sentence that nobody has reviewed.")


class ShareRequest(Model):
    """Body of POST .../providers/{contact_id}/share."""

    preview_hash: str = Field(description="`content_hash` of the preview the attorney confirmed.")
    override_reason: str | None = Field(
        None, max_length=1000, description="Why the attorney sends text the checker holds for a provider. Logged."
    )


class ShareLogEntry(Model):
    id: str
    contact_id: int
    link_href: str
    sent_at: str
    first_opened_at: str | None = None
    last_opened_at: str | None = None
    open_count: int = 0
    categories: list[DisclosureCategory] = Field(default_factory=list)
    item_count: int = 0
    state: str = Field("active", description="active | expired | revoked | superseded (a newer view was sent on the same link)")
    expires_at: str | None = None
    revoked_at: str | None = None
    snapshot_href: str | None = Field(None, description="GET returns the frozen ProviderView that was sent.")
    revoke_href: str | None = Field(None, description="POST to kill the link.")


class SuggestedAction(Model):
    """Firm side only: what would answer a provider's request, worked out in code."""

    action: str = Field(description="enable_category | resend | reply")
    category: DisclosureCategory | None = None
    label: str


class ProviderRequest(Model):
    """Something a provider asked the firm through their page."""

    id: str
    contact_id: int
    kind: str = Field(description="coverage | status | records_needed | payment_timing | other")
    question: str = Field(description="The kind in plain words.")
    text: str | None = Field(None, description="The provider's own words, if they added any.")
    created_at: str
    state: str = Field("open", description="open | answered | declined")
    answer: str | None = None
    answered_at: str | None = None
    suggested: SuggestedAction | None = Field(None, description="Never present in a ProviderView.")


class Message(Model):
    """One message in the thread between the firm and a provider, stored in our database."""

    id: str
    contact_id: int
    direction: str = Field(description="from_provider | from_firm")
    text: str
    at: str
    request_id: str | None = None


class ProviderThread(Model):
    """Response of GET /api/matters/{id}/providers/{contact_id}/thread."""

    requests: list[ProviderRequest] = Field(default_factory=list)
    thread: list[Message] = Field(default_factory=list)


class ProviderPanel(Model):
    """The firm's view of one treating provider."""

    contact: Contact
    records: RecordsStatus = Field(default_factory=RecordsStatus)
    bills: BillsStatus = Field(default_factory=BillsStatus)
    attendance: Attendance = Field(default_factory=Attendance)
    asks: list[Ask] = Field(default_factory=list)
    last_contact: Fact | None = None
    policy: SharePolicy
    preview_href: str = Field(description="GET returns the ProviderView this policy would produce.")
    shares: list[ShareLogEntry] = Field(default_factory=list)
    requests: list[ProviderRequest] = Field(default_factory=list, description="The inbox: newest first, each with a suggested action.")
    thread: list[Message] = Field(default_factory=list)


class ProviderView(Model):
    """Exactly what leaves the firm for one provider: the case model projected
    through that provider's SharePolicy. Built from the allowlist, so a category
    that is not listed in `shared_categories` is absent, not blanked. A sent link
    serves the view frozen at send time; only the provider's own replies are live."""

    contract_version: str = CONTRACT_VERSION
    generated_at: str
    provider: Contact
    client_name: str
    from_name: str | None = Field(None, description="Who at the firm sent this: the matter's responsible attorney in Clio.")
    firm_name: str | None = None
    attorney_name: str | None = None
    attorney_contact: str | None = Field(None, description="Email or phone, as the firm wants it shown.")
    message: str | None = None
    shared_categories: list[DisclosureCategory] = Field(default_factory=list)
    stage: Fact | None = None
    alive: Fact | None = None
    coverage: CoverageSignal
    updates: list[TimelineEvent] = Field(default_factory=list)
    records: RecordsStatus | None = None
    bills: BillsStatus | None = None
    attendance: Attendance | None = None
    asks: list[Ask] = Field(default_factory=list)
    requests: list[ProviderRequest] = Field(default_factory=list, description="This provider's own requests to the firm.")
    thread: list[Message] = Field(default_factory=list, description="Messages between this provider and the firm.")
    request_kinds: dict[str, str] = Field(default_factory=dict, description="kind -> the question to show on the 'Ask the firm' buttons.")
    expires_at: str | None = None
    content_hash: str | None = Field(
        None,
        description="SHA-256 over every field the attorney approves. Send the preview's value back when sharing;"
        " the frozen snapshot carries the same value.",
    )
    withheld_counts: dict[str, int] = Field(
        default_factory=dict, description="Preview only: items withheld per category. Empty on the shared link."
    )
    warnings: list[str] = Field(default_factory=list, description="Preview only: things the attorney should weigh.")


# --------------------------------------------------------------------------- digest bookkeeping


class ModelUsage(Model):
    model: str
    requests: int = 0
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class DigestStatus(Model):
    state: str = Field("not_started", description="not_started | running | partial | complete | failed")
    items_total: int = 0
    items_digested: int = 0
    pages_total: int = 0
    pages_digested: int = 0
    items_stale: int = Field(0, description="Items whose content hash changed since they were digested.")
    last_run_at: str | None = None
    quotes_total: int = Field(0, description="AI-derived quotes produced.")
    quotes_verified: int = Field(0, description="Of those, how many code found verbatim in the source text.")
    quotes_uncheckable: int = Field(
        0, description="Quotes read from scanned pages with no text layer: code cannot check them; the UI shows the page."
    )
    usage: list[ModelUsage] = Field(default_factory=list)
    cost_usd_total: float = Field(
        0.0, description="Cost per case: reading pages and records, and reconciliation. Excludes live checks."
    )
    reconcile_stale: bool = Field(
        False,
        description="True when records or pages have changed since the conflict cards were built. The cards,"
        " their order and their review states stay as they were until someone asks for a re-check.",
    )
    changed_since_reconcile: int = Field(0, description="Records and documents digested since the cards were built.")
    reconciled_at: str | None = None
    reconcile_estimate_seconds: float | None = Field(None, description="How long the last reconciliation took.")
    reconcile_estimate_usd: float | None = Field(None, description="What the last reconciliation cost.")
    reconcile_href: str | None = Field(None, description="POST to rebuild the cards from the current claims.")
    check_calls: int = Field(0, description="Model calls made by the live checker, reported separately.")
    check_cost_usd: float = 0.0
    check_cost_usd_avg: float | None = None
    cost_usd_last_run: float = Field(0.0, description="Cost of the most recent incremental run.")
    input_tokens_last_run: int = 0
    output_tokens_last_run: int = 0


class Meta(Model):
    contract_version: str = CONTRACT_VERSION
    matter_id: int
    generated_at: str
    synced_at: str | None = Field(None, description="Last successful read from Clio.")
    clio_counts: dict[str, int] = Field(default_factory=dict, description="Objects held per type.")
    digest: DigestStatus = Field(default_factory=DigestStatus)


# --------------------------------------------------------------------------- the case


class IncomingSpan(Model):
    """One sentence of something a party sent in, set against the file by the checker."""

    text: str
    verdict: str = Field(description="supported | contradicted | out_of_date | not_in_file")
    message: str = Field("", description="One line built in code from the ledger: what the file says.")
    claim_ids: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list, description="The file's side: open to see the note or page.")


class IncomingCheck(Model):
    """A communication already in Clio from someone outside the firm, checked sentence
    by sentence against the claims ledger: their statement versus your file. Firm side only."""

    id: str
    contact_id: int
    contact_name: str
    contact_role: str
    date: str | None = None
    subject: str | None = None
    counts: dict[str, int] = Field(default_factory=dict, description="Sentences per verdict.")
    spans: list[IncomingSpan] = Field(default_factory=list)
    source: SourceRef


class Move(Model):
    """One of the things to do now. Chosen and ranked in code: a hard deadline within
    14 days first, then the amount it touches, then how long it has waited."""

    id: str
    rank: int
    kind: str = Field(description="deadline | ask | conflict | node | task")
    title: str
    reason: str = Field(description="One line saying why it is on the list, built in code.")
    owed_by: str = Field(description="Who has the next step: 'firm' or the name of a contact.")
    owed_by_role: str | None = Field(None, description="firm | client | provider | adverse | insurer | other")
    audience_contact_id: int | None = Field(None, description="Recipient of `draft_text`, when it is a message.")
    waiting_days: int | None = None
    times_asked: int | None = Field(None, description="Messages the firm has logged to them since they last replied.")
    due: str | None = None
    amount: float | None = Field(None, description="USD the move touches; null when none is known.")
    node_id: str | None = None
    source: SourceRef
    draft_text: str | None = Field(None, description="A message for the 'draft' action, from a template in code.")
    category: DisclosureCategory = DisclosureCategory.internal


class CaseModel(Model):
    """Response of GET /api/matters/{matter_id}/case."""

    meta: Meta
    matter: Matter
    brief: Brief = Field(default_factory=Brief)
    key_facts: list[Fact] = Field(default_factory=list, description="The few entries that matter, ranked.")
    custom_fields: list[Fact] = Field(default_factory=list, description="Every Clio custom field value on the matter.")
    contacts: list[Contact] = Field(default_factory=list)
    nodes: list[ValueNode] = Field(default_factory=list)
    river: River = Field(default_factory=River)
    moves: list[Move] = Field(default_factory=list, description="Ranked; show the first three.")
    incoming: list[IncomingCheck] = Field(
        default_factory=list,
        description="Communications from outside the firm, checked against the file. Newest first; those with a"
        " contradicted or out-of-date sentence carry it in `counts`.",
    )
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    changes: Changes = Field(default_factory=Changes)
    agenda: Agenda
    timeline: list[TimelineEvent] = Field(default_factory=list)
    spend: Spend = Field(default_factory=Spend)
    documents: list[DocumentInfo] = Field(default_factory=list)
    providers: list[ProviderPanel] = Field(default_factory=list)


class MatterListItem(Model):
    """One row of GET /api/matters, read from Clio."""

    id: int
    display_number: str | None = None
    description: str | None = None
    status: str | None = None
    client_name: str | None = None
    synced_at: str | None = None
    source: str = Field("clio", description="clio = read from the source system; upload = created here and filled by upload.")
    archived: bool = Field(False, description="Hidden by the firm in our store; nothing is sent to the source system.")


class NewCase(Model):
    """Body of POST /api/matters: create an empty case in our own store."""

    name: str = Field(min_length=1, max_length=200)
    client_name: str | None = Field(None, max_length=200)
    number: str | None = Field(None, max_length=80)


class OverviewAgendaItem(Model):
    case_id: int
    case_name: str
    title: str
    date: str | None = None
    days_from_today: int | None = None
    kind: str = Field(description="task or calendar_entry")
    bucket: str = Field(description="overdue | waiting | coming")


class CaseOverview(Model):
    """One case on the lawyer-level overview. Built from stored data only. A value the
    case does not hold is null, never zero; the counts are real counts."""

    id: int
    name: str
    display_number: str | None = None
    client_name: str | None = None
    source: str = Field(description="clio | upload")
    source_label: str = Field(description="Imported | Uploaded: the word to print.")
    archived: bool = False
    status: str | None = None
    stage: str | None = None
    value: Fact | None = None
    coverage: Fact | None = None
    spend: Fact | None = None
    limitations: Fact | None = None
    next_deadline: OverviewAgendaItem | None = Field(None, description="The nearest dated task or calendar entry not yet past.")
    overdue: int = 0
    waiting: int = 0
    coming: int = 0
    to_review: int = Field(0, description="Differences between the firm's entries and its documents not yet reviewed.")
    last_activity: str | None = None
    documents: int = 0
    pages: int | None = Field(None, description="Null when the case has no documents.")
    pages_read: int = 0
    reading: str = Field(description="'12 of 40 pages read', 'not read yet' or 'no documents yet': print as given.")
    imported_at: str | None = Field(None, description="Last import from the source system; null for a case created here.")
    agenda: list[OverviewAgendaItem] = Field(default_factory=list, description="This case's dated open items.")


class OverviewTotals(Model):
    """Added up in code across the cases that are not archived. A money total is null when no case holds that figure."""

    cases: int = 0
    open_cases: int = 0
    archived: int = 0
    overdue: int = 0
    waiting: int = 0
    coming: int = 0
    due_14_days: int = 0
    to_review: int = 0
    value_total: float | None = None
    coverage_total: float | None = None
    spend_total: float | None = None


class FirmOverview(Model):
    """Response of GET /api/firm/overview."""

    generated_at: str
    cases: list[CaseOverview] = Field(default_factory=list)
    totals: OverviewTotals = Field(default_factory=OverviewTotals)
    agenda: list[OverviewAgendaItem] = Field(default_factory=list, description="The next dated items across all cases, soonest first.")
    warnings: list[str] = Field(default_factory=list)


class MatterList(Model):
    """Response of GET /api/matters."""

    connected: bool = Field(description="False when Clio is not connected; open `connect_href` to connect.")
    connect_href: str = "/oauth/start"
    live: bool = Field(False, description="True when the list was just read from Clio, false when from our cache.")
    selected_matter_id: int | None = None
    items: list[MatterListItem] = Field(default_factory=list)


class SyncResult(Model):
    """Response of POST /api/matters/{matter_id}/sync (a read from Clio into our database)."""

    matter_id: int
    requests: int
    changed: int
    kinds: dict[str, dict[str, int]] = Field(
        default_factory=dict, description="Per object type: total, new, updated, removed."
    )
    documents_downloaded: int = 0
    warnings: list[str] = Field(default_factory=list)


class ContractBundle(Model):
    """Not an API response. Exists so one schema file holds every shape the UI
    receives: export-schema writes this model's JSON Schema."""

    case: CaseModel
    provider_view: ProviderView
    source_detail: SourceDetail
    matters: MatterList
    sync_result: SyncResult
    new_case: NewCase
    firm_overview: FirmOverview
    provider_thread: ProviderThread
