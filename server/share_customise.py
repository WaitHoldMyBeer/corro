"""Customise a provider's share draft from one instruction in the attorney's words.

The attorney types what they want ("leave attendance off", "reword the request for
notes to ask for them by the 15th") and the draft for that provider changes before
anything is sent. The model's part is small and closed: it is shown the instruction
and this provider's candidate items as ids, categories and short labels, and it
answers with operations from a fixed set, by id. Everything that matters is decided
in code, not by the model:

- an operation that would share a never-shared category, that names an item which
  is not one of this provider's, or whose verb does not exist, is dropped and
  reported with a plain reason;
- text the model writes (a request's wording, the cover note) is read by the checker
  as that provider would receive it and passes the same guard as text the attorney
  types; text that is held is refused, not applied;
- operations change the draft only. Nothing is sent: the preview, the two-step send
  and the preview fingerprint are untouched, so a preview drawn before the instruction
  is refused on send.

Every instruction is logged with what was applied and what was refused. Firm side
only: the routes are under /api/matters/ and so behind the firm session guard.

Mounted by `server/app.py`, before the static files:

    from .share_customise import router as share_customise_router
    app.include_router(share_customise_router)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared import contract as c

from . import share
from .case import CaseBuilder, MatterNotSynced
from .config import Settings, get_settings
from .db import connect, now_iso
from .digest import llm, overlay, pipeline

router = APIRouter()

PURPOSE = "share_customise"

# One interactive call. The account's rate limit is shared with the digest, so a refused
# call is retried twice with the client's own backoff before the attorney is told.
TIMEOUT_SECONDS = 25.0
RETRIES = 2
OUTPUT_TOKENS = 2000

VERBS = ("set_category", "hide_item", "restore_item", "reword_ask", "set_cover_note", "reorder_sections")

# Why a category can never be switched on for a provider, in words the attorney reads.
NEVER = {
    "valuation": "Case value is never shared with a provider.",
    "strategy": "Case strategy is never shared with a provider.",
    "other_party": "Information about other parties is never shared with a provider.",
    "internal": "The firm's internal material is never shared with a provider.",
}

CATEGORY_NAMES = {
    "status": "Status", "bills": "Bills", "records": "Records", "attendance": "Attendance",
    "asks": "Requests to their office", "coverage": "Coverage",
}

# Said wherever the assistant's wording is still in the draft: the attorney asked for it but did not type it.
AI_WORDING = "Wording suggested by AI: read it before sending."

HELD = {
    share.DISCLOSES: "it discloses something this provider is never shown",
    share.UNCHECKED: "the checker's model has not read it yet; try again in a moment",
    share.UNAVAILABLE: "the checker could not be run",
}

INSTRUCTIONS = """You turn a lawyer's instruction about what one medical provider's office is shown into operations on a share draft.

You are given the instruction, optionally one target item it is about, and the draft: its categories (on or off), its items (id, category, label, state), and the current cover note.

Answer only with operations from this set:
- set_category(category, on): switch one of the listed categories on or off.
- hide_item(id): take one listed item out of what the provider sees.
- restore_item(id): put a hidden listed item back.
- reword_ask(id, text): set the wording of one listed request to the provider's office. `text` is the full new wording.
- set_cover_note(text): set the cover note. `text` is the full new note; an empty text removes it.
- reorder_sections(sections): the order of the category sections, as category names.

Rules:
- Use only ids and category names that appear in the draft. Never invent an id.
- Do exactly what the instruction asks and nothing more. If it is about the target item, act only on that item.
- Text you write goes to the provider's office: write it plainly, as the firm speaking to them. Use only what the instruction and the existing wording give you; add no figure, date, name or fact of your own.
- If a part of the instruction cannot be done with these operations, do not approximate it: put that part, in the lawyer's own words, in not_done.
- Fill only the fields an operation uses and leave the others null."""


class ProposedOperation(BaseModel):
    """One operation as the model returns it. The verb list is closed here and checked again in code."""

    verb: Literal["set_category", "hide_item", "restore_item", "reword_ask", "set_cover_note", "reorder_sections"]
    category: str | None
    on: bool | None
    id: str | None
    text: str | None
    sections: list[str] | None


class Proposal(BaseModel):
    operations: list[ProposedOperation]
    not_done: list[str]


class Target(c.Model):
    """What an instruction is about, as the page names it: an item id, a category, the cover
    note, or only a section's heading. It is resolved in code (`resolve`); a target that
    cannot be resolved means the whole share."""

    kind: str = Field(max_length=40, description="ask | update | category | cover_note; item and message are accepted as other names.")
    id: str | None = Field(None, max_length=200)


class CustomiseRequest(c.Model):
    """Body of POST /api/matters/{id}/providers/{contact_id}/customise."""

    instruction: str = Field(min_length=1, max_length=1000)
    target: Target | None = None
    draft_policy: c.SharePolicy | None = Field(None, description="The draft as the page holds it; the stored policy when left out.")


class Operation(c.Model):
    verb: str
    category: str | None = None
    on: bool | None = None
    id: str | None = None
    text: str | None = None
    sections: list[str] | None = None
    summary: str = Field("", description="What the operation did, one line built in code.")


class Refusal(c.Model):
    request: str = Field(description="The operation, or the part of the instruction, that was not carried out.")
    reason: str = Field(description="Why, built in code.")


class Customised(c.Model):
    """Response of the customise route: what changed in the draft and what was refused. Nothing was sent."""

    operations: list[Operation] = Field(default_factory=list)
    refused: list[Refusal] = Field(default_factory=list)
    reply: str
    policy: c.SharePolicy = Field(description="The draft after the applied operations, saved as this provider's policy.")
    content_hash: str | None = Field(None, description="Fingerprint of the preview this draft now produces.")


class LoggedInstruction(c.Model):
    at: str
    instruction: str
    target: Target | None = None
    operations: list[Operation] = Field(default_factory=list)
    refused: list[Refusal] = Field(default_factory=list)


class SuggestedWording(c.Model):
    """Which text in the draft the assistant wrote and the attorney has neither edited nor sent yet."""

    cover_note: bool = False
    asks: list[str] = Field(default_factory=list, description="Ids of requests whose approved wording is the assistant's.")
    note: str = AI_WORDING


class ModelUnavailable(RuntimeError):
    pass


# A stand-in for the model: (cfg, instruction, target, draft description) -> proposed operations and parts not done.
Proposer = Callable[[Settings, str, Target | None, dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]


# --------------------------------------------------------------------------- what the model is shown


def candidates(case: c.CaseModel, contact_id: int, policy: c.SharePolicy) -> dict[str, Any]:
    """This provider's draft as the model sees it: categories, and the items that could be in
    its view, each as id, category, a short label and its state. Nothing outside the allowlist
    projection is listed, so nothing outside it can be named in an operation."""
    panel = next((p for p in case.providers if p.contact.id == contact_id), None)
    if panel is None:
        raise LookupError(f"contact {contact_id} is not a treating provider on this matter")
    hidden, allowed = set(policy.hidden_item_ids), set(policy.allowed_categories)
    items = []
    for event in sorted(case.timeline, key=lambda event: event.date):
        outbound = share._outbound_event(event, contact_id)
        if outbound is not None:
            items.append({
                "id": outbound.id, "kind": "update", "category": outbound.category, "label": f"{outbound.label} ({outbound.date})",
                "state": "hidden" if outbound.id in hidden else "shown" if outbound.category in allowed else "category off",
            })
    for ask in panel.asks:
        wording = policy.approved_asks.get(ask.id, "").strip()
        items.append({
            "id": ask.id, "kind": "ask", "category": "asks", "label": wording or ask.text,
            "state": "hidden" if ask.id in hidden else "approved wording" if wording else "not approved, not sent",
        })
    return {
        "categories": [{"category": name, "on": name in allowed} for name in CATEGORY_NAMES],
        "items": items,
        "cover_note": policy.message or "",
    }


# What a page may call the cover note, and the headings a section may be known by, lower case.
COVER_NOTE_NAMES = {"cover_note", "cover note", "message", "note"}


def resolve(target: Target | None, draft: dict[str, Any]) -> Target | None:
    """The target as one of this provider's own things, or None for the whole share. An id is
    matched against the draft's items; a heading or a category name against the closed list
    of categories. Nothing outside the draft can be named this way."""
    if target is None:
        return None
    kind, given = target.kind.strip().lower(), (target.id or "").strip()
    items = {item["id"]: item for item in draft["items"]}
    if given in items:
        return Target(kind=items[given]["kind"], id=given)
    if kind in COVER_NOTE_NAMES or given.lower() in COVER_NOTE_NAMES:
        return Target(kind="cover_note")
    headings = {name: name for name in CATEGORY_NAMES} | {label.lower(): name for name, label in CATEGORY_NAMES.items()}
    for words in (given.lower(), kind):
        if words in headings:
            return Target(kind="category", id=headings[words])
    return None


def propose(cfg: Settings, instruction: str, target: Target | None, draft: dict[str, Any], record=None) -> tuple[list[dict[str, Any]], list[str]]:
    """Ask the small model for operations. It is given the instruction and the draft's ids, categories and labels."""
    model = cfg.check_model or cfg.digest_model_bulk
    about = f"\n\nThe instruction is about this one item: {json.dumps(target.model_dump())}" if target else ""
    try:
        out, usage = llm.structured(
            cfg, model=model, instructions=INSTRUCTIONS,
            content=[llm.text_part("The draft:\n" + json.dumps(draft, ensure_ascii=False)), llm.text_part("The lawyer's instruction:\n" + instruction + about)],
            schema=Proposal, effort="low", max_output_tokens=OUTPUT_TOKENS, timeout=TIMEOUT_SECONDS, max_retries=RETRIES,
        )
    except llm.LLMUsageError as error:
        if record:
            record(error.usage, False)
        raise ModelUnavailable(str(error)) from error
    except llm.LLMError as error:
        raise ModelUnavailable(str(error)) from error
    if record:
        record(usage, True)
    return [operation.model_dump() for operation in out.operations], list(out.not_done)


# --------------------------------------------------------------------------- the rules, in code


def _words(operation: dict[str, Any]) -> str:
    """An operation in plain words, for the list of what was refused."""
    verb = operation.get("verb")
    if verb == "set_category":
        return f"switch {operation.get('category')} {'on' if operation.get('on') else 'off'}"
    if verb in ("hide_item", "restore_item"):
        return f"{'hide' if verb == 'hide_item' else 'restore'} item {operation.get('id')}"
    if verb == "reword_ask":
        return f"reword request {operation.get('id')}"
    if verb == "set_cover_note":
        return "set the cover note"
    if verb == "reorder_sections":
        return "reorder the sections"
    return f"operation {verb!r}"


def _held(locked: list[dict]) -> str:
    """Why written text was refused, without repeating more of it than the held sentence."""
    first = locked[0]
    category = f" ({first['category']})" if first.get("category") else ""
    sentence = f": “{first['text']}”" if first.get("text") else ""
    more = f", and {len(locked) - 1} more" if len(locked) > 1 else ""
    return f"The wording was not applied because {HELD.get(first['state'], 'it was held')}{category}{sentence}{more}."


def apply(
    policy: c.SharePolicy,
    draft: dict[str, Any],
    proposed: list[dict[str, Any]],
    target: Target | None,
    checker: share.Checker | None,
) -> tuple[c.SharePolicy, list[Operation], list[Refusal]]:
    """Carry out the proposed operations on a copy of the policy. Each one is checked here,
    whatever proposed it: the verb must exist, a category must be shareable, an id must be one
    of this provider's items, and written text must pass the checker's guard. Returns the new
    policy, what was applied and what was refused."""
    policy = policy.model_copy(deep=True)
    items = {item["id"]: item for item in draft["items"]}
    applied, refused = [], []

    def refuse(operation: dict[str, Any], reason: str) -> None:
        refused.append(Refusal(request=_words(operation), reason=reason))

    def done(operation: dict[str, Any], summary: str) -> None:
        fields = {name: operation.get(name) for name in ("verb", "category", "on", "id", "text", "sections")}
        applied.append(Operation(**fields, summary=summary))

    def off_target(kind: str, item_id: str | None) -> bool:
        if target is None:
            return False
        return target.kind != kind or (target.id is not None and target.id != item_id)

    def written(operation: dict[str, Any], item: str) -> str | None:
        """The text of an operation if it may go to this provider; None after refusing it."""
        text = " ".join(str(operation.get("text") or "").split())
        if not text:
            return ""
        if len(text) > share.MAX_ASK_CHARS and operation["verb"] == "reword_ask":
            refuse(operation, f"The wording is longer than {share.MAX_ASK_CHARS} characters.")
            return None
        if checker is None:
            refuse(operation, "The wording was not applied because the checker could not be run.")
            return None
        locked = share.locked_text([(item, text)], checker)
        if locked:
            refuse(operation, _held(locked))
            return None
        return text

    for operation in proposed:
        verb = operation.get("verb")
        if verb not in VERBS:
            refuse(operation, "No such operation exists.")
        elif verb == "set_category":
            category = operation.get("category")
            if category in NEVER:
                refuse(operation, NEVER[category])
            elif category not in share.SHAREABLE:
                refuse(operation, "That is not a category of the share.")
            elif off_target("category", category):
                refuse(operation, "The instruction was about one item only.")
            elif not isinstance(operation.get("on"), bool):
                refuse(operation, "The operation did not say whether to switch it on or off.")
            else:
                allowed = [name for name in policy.allowed_categories if name != category]
                policy.allowed_categories = [*allowed, category] if operation["on"] else allowed
                done(operation, f"{CATEGORY_NAMES[category]} switched {'on' if operation['on'] else 'off'}")
        elif verb in ("hide_item", "restore_item"):
            item = items.get(operation.get("id"))
            if item is None:
                refuse(operation, "That item is not one of this provider's.")
            elif off_target(item["kind"], item["id"]):
                refuse(operation, "The instruction was about one item only.")
            else:
                kept = [item_id for item_id in policy.hidden_item_ids if item_id != item["id"]]
                policy.hidden_item_ids = [*kept, item["id"]] if verb == "hide_item" else kept
                done(operation, f"{'Hidden' if verb == 'hide_item' else 'Restored'}: {item['label']}")
        elif verb == "reword_ask":
            item = items.get(operation.get("id"))
            if item is None or item["kind"] != "ask":
                refuse(operation, "That request is not one of this provider's.")
            elif off_target("ask", item["id"]):
                refuse(operation, "The instruction was about one item only.")
            else:
                text = written(operation, item["id"])
                if text == "":
                    refuse(operation, "No wording was given.")
                elif text is not None:
                    policy.approved_asks = {**policy.approved_asks, item["id"]: text}
                    done({**operation, "text": text}, f"Request reworded and approved. {AI_WORDING}")
        elif verb == "set_cover_note":
            if off_target("cover_note", None):
                refuse(operation, "The instruction was about one item only.")
            else:
                text = written(operation, "cover_note")
                if text is not None:
                    policy.message = text or None
                    done({**operation, "text": text}, f"Cover note set. {AI_WORDING}" if text else "Cover note removed")
        else:  # reorder_sections
            sections = operation.get("sections") or []
            if any(name in NEVER for name in sections):
                refuse(operation, NEVER[next(name for name in sections if name in NEVER)])
            elif any(name not in share.SHAREABLE for name in sections) or len(sections) != len(set(sections)):
                refuse(operation, "The order names something that is not a section of the share.")
            elif "section_order" not in c.SharePolicy.model_fields:
                refuse(operation, "Section order cannot be changed yet.")
            else:
                policy.section_order = list(sections)
                done(operation, "Sections reordered")
    return policy, applied, refused


def _reply(applied: list[Operation], refused: list[Refusal]) -> str:
    """What happened, in two sentences at most, built from the lists."""
    if not applied and not refused:
        return "Nothing in the draft needed changing for that instruction."
    parts = []
    if applied:
        parts.append(f"Changed the draft: {'; '.join(operation.summary for operation in applied)}.")
    if refused:
        parts.append(f"Not done: {'; '.join(refusal.reason for refusal in refused)}".rstrip(".") + ".")
    return " ".join(parts) + " Nothing has been sent."


# --------------------------------------------------------------------------- the log


def _log_table(conn: sqlite3.Connection) -> None:
    # Created here on first use so this module needs no change elsewhere; the same statement can live in the schema.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS share_instructions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, matter_id INTEGER NOT NULL, contact_id INTEGER NOT NULL,"
        " instruction TEXT NOT NULL, target TEXT, operations TEXT NOT NULL, refused TEXT NOT NULL, at TEXT NOT NULL)"
    )


def log(conn: sqlite3.Connection, matter_id: int, contact_id: int, request: CustomiseRequest, applied: list[Operation], refused: list[Refusal]) -> None:
    _log_table(conn)
    conn.execute(
        "INSERT INTO share_instructions (matter_id, contact_id, instruction, target, operations, refused, at) VALUES (?,?,?,?,?,?,?)",
        (
            matter_id, contact_id, request.instruction, request.target.model_dump_json() if request.target else None,
            json.dumps([operation.model_dump() for operation in applied]), json.dumps([refusal.model_dump() for refusal in refused]), now_iso(),
        ),
    )
    conn.commit()


def history(conn: sqlite3.Connection, matter_id: int, contact_id: int) -> list[LoggedInstruction]:
    _log_table(conn)
    rows = conn.execute(
        "SELECT * FROM share_instructions WHERE matter_id=? AND contact_id=? ORDER BY id DESC", (matter_id, contact_id)
    ).fetchall()
    return [
        LoggedInstruction(
            at=row["at"], instruction=row["instruction"], target=json.loads(row["target"]) if row["target"] else None,
            operations=json.loads(row["operations"]), refused=json.loads(row["refused"]),
        )
        for row in rows
    ]


def suggested(conn: sqlite3.Connection, matter_id: int, policy: c.SharePolicy) -> SuggestedWording:
    """The assistant's wording that is still in the draft as it wrote it. An approval made by
    `reword_ask` is one the attorney did not type, so it stays marked until the attorney changes
    the text or sends the share."""
    _log_table(conn)
    rows = conn.execute(
        "SELECT operations, at FROM share_instructions WHERE matter_id=? AND contact_id=? ORDER BY id DESC",
        (matter_id, policy.contact_id),
    ).fetchall()
    last_sent = conn.execute(
        "SELECT MAX(sent_at) FROM shares WHERE matter_id=? AND contact_id=?", (matter_id, policy.contact_id)
    ).fetchone()[0]
    latest: dict[str, str] = {}  # item -> the assistant's most recent wording for it, if not sent since
    for row in rows:
        for operation in json.loads(row["operations"]):
            item = operation.get("id") if operation["verb"] == "reword_ask" else "cover_note" if operation["verb"] == "set_cover_note" else None
            if item is not None and item not in latest:
                latest[item] = operation.get("text") or "" if not last_sent or row["at"] > last_sent else ""
    return SuggestedWording(
        cover_note=bool(latest.get("cover_note")) and latest["cover_note"] == (policy.message or ""),
        asks=sorted(ask_id for ask_id, text in policy.approved_asks.items() if text and latest.get(ask_id) == text),
    )


# --------------------------------------------------------------------------- one instruction, end to end


def customise(
    cfg: Settings,
    build: CaseBuilder,
    contact_id: int,
    request: CustomiseRequest,
    *,
    proposer: Proposer | None = None,
    checker: share.Checker | None = None,
) -> Customised:
    """Apply one instruction to this provider's draft and save the draft. Nothing is sent."""
    case = overlay.apply(cfg, build, build.build())
    policy = request.draft_policy or build.policy(contact_id)
    policy.contact_id = contact_id
    draft = candidates(case, contact_id, policy)
    target = resolve(request.target, draft)

    def record(usage: llm.Usage, ok: bool) -> None:
        pipeline.record_call(build.conn, build.matter_id, None, PURPOSE, usage, ok)

    if proposer is not None:
        proposed, not_done = proposer(cfg, request.instruction, target, draft)
    else:
        proposed, not_done = propose(cfg, request.instruction, target, draft, record)
    checker = checker or share.provider_checker(cfg, build.conn, build.matter_id, contact_id)
    policy, applied, refused = apply(policy, draft, proposed, target, checker)
    refused += [Refusal(request=part, reason="No operation on a share draft does this.") for part in not_done if str(part).strip()]
    try:
        policy = share.save_policy(build.conn, build.matter_id, policy)
    except share.NotShareable as error:  # cannot happen after `apply`; kept so a never-shared category can never be saved from here
        raise HTTPException(422, str(error)) from error
    log(build.conn, build.matter_id, contact_id, request, applied, refused)
    preview = share.provider_view(case, contact_id, policy, preview=True)
    return Customised(operations=applied, refused=refused, reply=_reply(applied, refused), policy=policy, content_hash=preview.content_hash)


# --------------------------------------------------------------------------- routes


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _builder(matter_id: int, conn: Annotated[sqlite3.Connection, Depends(_db)]) -> CaseBuilder:
    try:
        return CaseBuilder(conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error


def _provider_or_404(build: CaseBuilder, contact_id: int) -> None:
    contact = build.contact_by_id.get(contact_id)
    if contact is None or contact.role != c.ContactRole.provider.value:
        raise HTTPException(404, "not a treating provider on this matter")


@router.post("/api/matters/{matter_id}/providers/{contact_id}/customise", response_model=Customised)
def customise_share(
    contact_id: int,
    request: CustomiseRequest,
    cfg: Annotated[Settings, Depends(_settings)],
    build: Annotated[CaseBuilder, Depends(_builder)],
) -> Customised:
    """Change this provider's share draft from one instruction. The draft is saved; nothing is sent.
    503 when the model is not answering, in which case nothing is changed."""
    _provider_or_404(build, contact_id)
    try:
        return customise(cfg, build, contact_id, request)
    except ModelUnavailable as error:
        busy = "RateLimit" in str(error)
        raise HTTPException(
            503,
            "The model account is at its limit or out of credit, so the assistant cannot answer; the draft is unchanged."
            if busy
            else "The assistant is not answering just now; the draft is unchanged.",
        ) from error


@router.get("/api/matters/{matter_id}/providers/{contact_id}/customise", response_model=list[LoggedInstruction])
def customise_history(contact_id: int, build: Annotated[CaseBuilder, Depends(_builder)]) -> list[LoggedInstruction]:
    """The instructions given for this provider's share, newest first, with what each changed and what was refused."""
    _provider_or_404(build, contact_id)
    return history(build.conn, build.matter_id, contact_id)


@router.get("/api/matters/{matter_id}/providers/{contact_id}/customise/suggested", response_model=SuggestedWording)
def suggested_wording(contact_id: int, build: Annotated[CaseBuilder, Depends(_builder)]) -> SuggestedWording:
    """Which wording in this provider's draft is the assistant's and not yet edited or sent, so the
    preview can say so beside it."""
    _provider_or_404(build, contact_id)
    return suggested(build.conn, build.matter_id, build.policy(contact_id))
