"""Shapes the model must return. Strict JSON schema: every field required,
nothing extra. Anything the model returns is validated against these before use."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

FactKind = Literal[
    "injury_or_diagnosis",
    "treatment",
    "charge_or_balance",
    "incident_fact",
    "liability_fact",
    "insurance_or_coverage",
    "employment_or_income",
    "person_or_witness",
    "legal_event",
    "valuation",
    "other",
]

PageType = Literal[
    "medical_record",
    "bill_or_ledger",
    "incident_or_police_report",
    "sworn_statement",
    "court_filing",
    "correspondence",
    "insurance_document",
    "authorization_or_intake_form",
    "identification",
    "photograph",
    "cover_or_blank",
    "other",
]

Category = Literal[
    "status", "bills", "records", "attendance", "asks", "coverage", "valuation", "strategy", "other_party", "internal"
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- pages of documents -------------------------------------------------------


class PageFact(Strict):
    kind: FactKind
    statement: str
    quote: str
    date: str | None
    amount_usd: float | None
    party: str | None


class Checkbox(Strict):
    label: str
    checked: bool


class PageRead(Strict):
    page: int
    page_type: PageType
    title: str | None
    issuer: str | None
    document_date: str | None
    shows_photo_of_a_person: bool
    checkboxes: list[Checkbox]
    facts: list[PageFact]


class PageBatch(Strict):
    pages: list[PageRead]


class PhotoBox(Strict):
    found: bool
    left: float
    top: float
    right: float
    bottom: float


# -- Clio text items ----------------------------------------------------------


class TextClaim(Strict):
    item: str
    kind: FactKind
    topic: str
    statement: str
    quote: str
    date: str | None
    amount_usd: float | None
    party: str | None
    category: Category


class TextClaims(Strict):
    claims: list[TextClaim]


# -- reconciliation -----------------------------------------------------------


class ConflictOut(Strict):
    topic: str
    summary: str
    notes_claim_ids: list[str]
    document_claim_ids: list[str]
    entry_position: Literal["outstanding", "different"]
    affects: Literal["case_value", "coverage", "liability", "liens", "damages", "none"]
    severity: Literal[1, 2, 3]


class NewConflicts(Strict):
    conflicts: list[ConflictOut]


class KeyFactOut(Strict):
    label: str
    kind: Literal["primary_injury", "liability", "coverage", "damages", "procedure", "other"]
    claim_ids: list[str]


class EventOut(Strict):
    claim_id: str
    label: str
    kind: Literal["treatment", "legal", "communication", "billing", "records", "other"]
    importance: Literal[1, 2, 3]


class NodeEvidence(Strict):
    node: Literal["value", "coverage", "gate", "lien", "cost", "fee"]
    claim_ids: list[str]
    open_question: str | None


class EconomicOut(Strict):
    label: str
    claim_id: str


class SummaryOut(Strict):
    sentence: str
    claim_ids: list[str]


class IssuerMatch(Strict):
    issuer: str
    contact_id: int | None


class Reconciled(Strict):
    conflicts: list[ConflictOut]
    key_facts: list[KeyFactOut]
    events: list[EventOut]
    node_evidence: list[NodeEvidence]
    economics: list[EconomicOut]
    summary: list[SummaryOut]
    issuers: list[IssuerMatch]
