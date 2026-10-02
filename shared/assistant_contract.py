"""The assistant's request and response: what the panel sends and what it renders.

An answer is a list of blocks, never loose prose. Every sentence, table row and
document entry carries reference ids; `citations` resolves each one, in code,
to the source it opens and the graph node it lights. A reference the model
gives that is not in the file is dropped before this shape is built, and a
sentence left with none is marked `grounded: false`.

No field has a default that carries a case fact.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ASSISTANT_CONTRACT_VERSION = "0.1.0"

Mode = Literal["fast", "deep"]
DocumentKind = Literal[
    "medical_chronology", "records_summary", "damages_summary", "bills_liens_summary", "provider_requests", "case_summary"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- request


class ContextItem(Model):
    """Something the lawyer dragged into the panel. Data to reason about, never instructions."""

    kind: Literal["card", "passage", "document", "claim", "node", "record"]
    id: str | None = Field(None, max_length=200)
    label: str | None = Field(None, max_length=300)
    text: str | None = Field(None, max_length=4000)
    data: dict[str, Any] | None = None


class AssistantRequest(Model):
    message: str = Field(min_length=1, max_length=4000)
    mode: Mode = "fast"
    conversation_id: str | None = Field(None, max_length=64)
    context_items: list[ContextItem] = Field(default_factory=list, max_length=12)
    stream: bool = False
    fresh: bool = Field(False, description="True works the answer out again instead of returning the saved one (Regenerate).")


# --------------------------------------------------------------------------- citations


class Citation(Model):
    """One reference, resolved in code. `node_id` is the graph payload's node id."""

    id: str = Field(description="A claim id, or '<kind>:<clio_id>' for a whole record.")
    claim_id: str | None = None
    node_id: str
    kind: str
    clio_id: int | str
    page: int | None = None
    label: str
    date: str | None = None
    text: str | None = Field(None, description="The claim's statement, or the record's title.")
    quote: str | None = None
    quote_verified: bool = False
    href: str = Field(description="GET returns the SourceDetail the drawer shows.")


# --------------------------------------------------------------------------- blocks


class Sentence(Model):
    text: str
    cite: list[str] = Field(default_factory=list)
    grounded: bool = Field(False, description="False when no citation survived: show 'not in the file'.")
    figures_unverified: list[str] = Field(
        default_factory=list, description="Amounts and dates in the sentence that code did not find in its cited sources."
    )


class TableRow(Model):
    cells: list[str]
    cite: list[str] = Field(default_factory=list)


class DocumentColumn(Model):
    key: str
    label: str


class DocumentEntry(Model):
    date: str | None = None
    date_basis: str | None = Field(None, description="'document' = printed on the page; 'record' = the record's own date.")
    provider: str | None = Field(None, description="One label per provider: the matter's contact when the printed name matches one.")
    provider_as_printed: str | None = Field(None, description="The name as the page prints it, when it differs from `provider`.")
    what: str
    amount: float | None = Field(None, description="USD, when the row is money.")
    group: str | None = None
    kind: str | None = None
    source_label: str | None = None
    page: int | None = None
    cite: list[str] = Field(default_factory=list)


class DocumentGroup(Model):
    label: str
    entries: int
    first: str | None = None
    last: str | None = None
    total: float | None = Field(None, description="USD, added in code.")


class DocumentTotals(Model):
    entries: int = 0
    amount: float | None = None
    undated: int = 0


class AssistantDocument(Model):
    """A document assembled and ordered in code from claims the assistant selected."""

    id: str
    kind: DocumentKind
    title: str
    created_at: str
    ledger_version: str
    conversation_id: str | None = None
    columns: list[DocumentColumn] = Field(default_factory=list)
    entries: list[DocumentEntry] = Field(default_factory=list)
    groups: list[DocumentGroup] = Field(default_factory=list)
    totals: DocumentTotals = Field(default_factory=DocumentTotals)
    href: str
    pdf_href: str | None = Field(None, description="GET returns the document as a PDF, rendered from our store on request.")


class Block(Model):
    """One of: heading (text), paragraph (sentences), table (title, columns, rows), document."""

    type: Literal["heading", "paragraph", "table", "document"]
    text: str | None = None
    sentences: list[Sentence] | None = None
    title: str | None = None
    columns: list[str] | None = None
    rows: list[TableRow] | None = None
    document: AssistantDocument | None = None


# --------------------------------------------------------------------------- trace and usage


class TraceStep(Model):
    """One tool call: what was asked for and how much came back. A step whose tool is
    `model` is one round of the model itself: its plan, its time and its tokens."""

    n: int
    round: int
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    items: int = 0
    ms: float = 0.0
    cached: bool = False
    ok: bool = True
    error: str | None = None
    summary: str | None = None
    input_tokens: int | None = Field(None, description="Set on a `model` step: what that round read.")
    output_tokens: int | None = None
    cost_usd: float | None = None


class TurnUsage(Model):
    rounds: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    model_seconds: float = 0.0
    tool_ms: float = 0.0


class AssistantTurn(Model):
    """Response of POST /api/matters/{id}/assistant, and the `done` event of its stream."""

    contract_version: str = ASSISTANT_CONTRACT_VERSION
    conversation_id: str
    turn_id: str
    at: str
    mode: Mode
    model: str
    question: str
    ledger_version: str
    blocks: list[Block] = Field(default_factory=list)
    citations: dict[str, Citation] = Field(default_factory=dict)
    trace: list[TraceStep] = Field(default_factory=list)
    usage: TurnUsage = Field(default_factory=TurnUsage)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- listings


class ConversationDocument(Model):
    id: str
    title: str
    kind: DocumentKind
    pdf_href: str


class ConversationInfo(Model):
    id: str
    title: str = Field(description="Made in code from the first message, or the name the lawyer gave it.")
    created_at: str | None = None
    updated_at: str
    turns: int
    documents: list[ConversationDocument] = Field(default_factory=list, description="Documents created in this conversation.")


class Conversation(Model):
    id: str
    title: str
    turns: list[AssistantTurn] = Field(default_factory=list)


class DocumentInfo(Model):
    id: str
    kind: DocumentKind
    title: str
    created_at: str
    entries: int
    citations: int = 0
    pages: int | None = Field(None, description="Page count of the PDF; null until it has been rendered once.")
    conversation_id: str | None = None
    href: str
    pdf_href: str


class SavedDocument(Model):
    document: AssistantDocument
    citations: dict[str, Citation] = Field(default_factory=dict)


class DocumentKindInfo(Model):
    """A kind of document the server can build in code, offered as a starting point."""

    kind: DocumentKind
    title: str
    description: str
    prompt: str = Field(description="A ready message for the assistant that asks for this document.")


class DocumentRequest(Model):
    """Body of POST .../assistant/documents: build one in code, with no model call."""

    kind: DocumentKind
    title: str | None = Field(None, max_length=140)
    date_from: str | None = Field(None, max_length=10)
    date_to: str | None = Field(None, max_length=10)
    provider: str | None = Field(None, max_length=200)


class RenameRequest(Model):
    title: str = Field(min_length=1, max_length=120)
