"""Nothing outside a provider's allowlist, and nothing that belongs to another
provider, may survive into the provider view or the frozen share snapshot.

The case here is synthetic and generated: every free-text field of every item
carries a marker that says which category it is, whose it is and where it sat.
The test projects that case for every provider under every combination of
shareable categories, serialises what would leave the firm, and reads the
markers back out. A marker that should not be there names its own origin.
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import date
from types import SimpleNamespace

import pytest

from server import share
from server.db import connect
from shared import contract as c

# The fixture's one date, built here rather than written out, so that no date literal in this file
# can equal a date held in whatever store the hardcode check derives its terms from.
DAY = date(1900, 1, 1).isoformat()

MATTER_ID = 7
CLIENT, PROVIDER_A, PROVIDER_B, ADVERSE = 100, 101, 102, 103
PROVIDERS = (PROVIDER_A, PROVIDER_B)
CATEGORIES = [category.value for category in c.DisclosureCategory]
SHAREABLE = [category.value for category in c.SHAREABLE_CATEGORIES]
FIRM_ONLY = [category for category in CATEGORIES if category not in SHAREABLE]

MARK = re.compile(r"«([a-z_]+)\|([a-z0-9_]+)\|([a-z_]+)\|([a-z0-9_.:-]+)»")


def mark(category: str, owner: int | str, origin: str, field: str) -> str:
    """category | owner (contact id, 'none' or 'firm') | origin (clio or ai) | where it sat."""
    return f"«{category}|{owner}|{origin}|{field}»"


def ref(field: str) -> c.SourceRef:
    """A firm-side source link. Its label, quote and href must never leave the firm."""
    return c.SourceRef(
        kind="note",
        clio_id=1,
        label=mark("internal", "firm", "clio", f"{field}.source.label"),
        quote=mark("internal", "firm", "clio", f"{field}.source.quote"),
        href=f"/api/matters/{MATTER_ID}/sources/note/1",
    )


def fact(fact_id: str, category: str, amount: float | None = None) -> c.Fact:
    return c.Fact(
        id=fact_id,
        label=mark(category, "none", "clio", f"{fact_id}.label"),
        display=mark(category, "none", "clio", f"{fact_id}.display"),
        detail=mark("internal", "firm", "clio", f"{fact_id}.detail"),
        amount=amount,
        derivation="clio",
        category=category,
        sources=[ref(fact_id)],
        conflict_ids=["conflict:1"],
    )


def contact(contact_id: int, role: str) -> c.Contact:
    return c.Contact(
        id=contact_id,
        name=f"Synthetic contact {contact_id}",
        type="Company",
        role=role,
        role_text=mark("internal", "firm", "clio", f"contact{contact_id}.role_text"),
        source=ref(f"contact{contact_id}"),
    )


# Figures that must be recognisable if they leak. Each provider's own are distinct.
VALUATION_FIGURES = (9_870_001.25, 9_870_002.5, 9_870_003.75)
PAGES = {PROVIDER_A: 70_001, PROVIDER_B: 70_002}
BILLED = {PROVIDER_A: 410_001.11, PROVIDER_B: 420_002.22}
VISITS = {PROVIDER_A: 50_001, PROVIDER_B: 50_002}


def panel(contact_id: int) -> c.ProviderPanel:
    return c.ProviderPanel(
        contact=contact(contact_id, "provider"),
        records=c.RecordsStatus(state="received", pages=PAGES[contact_id], sources=[ref("records")]),
        bills=c.BillsStatus(state="received", billed_total=BILLED[contact_id], sources=[ref("bills")]),
        attendance=c.Attendance(signal="recorded", visits=VISITS[contact_id], sources=[ref("attendance")]),
        asks=[
            c.Ask(
                id=f"ask:{contact_id}:{n}",
                contact_id=contact_id,
                text=mark("asks", contact_id, "clio", f"ask{n}.text"),
                node_id="gate",
                sources=[ref("ask")],
            )
            for n in range(2)
        ],
        last_contact=fact(f"last_contact:{contact_id}", "internal"),
        policy=c.SharePolicy(contact_id=contact_id),
        preview_href=f"/api/matters/{MATTER_ID}/providers/{contact_id}/preview",
    )


def timeline() -> list[c.TimelineEvent]:
    """One event for every category x owner x derivation."""
    events = []
    for category, owner, derivation in itertools.product(CATEGORIES, (None, PROVIDER_A, PROVIDER_B, ADVERSE), ("clio", "ai")):
        event_id = f"event:{category}:{owner}:{derivation}"
        events.append(
            c.TimelineEvent(
                id=event_id,
                date=DAY,
                label=mark(category, owner or "none", derivation, "event.label"),
                contact_id=owner,
                claim_ids=["claim:1"],
                node_deltas={"value": VALUATION_FIGURES[0]},
                category=category,
                derivation=derivation,
                sources=[ref("event")],
            )
        )
    return events


def synthetic_case() -> c.CaseModel:
    firm_lists = {
        name: [fact(f"{name}:{category}", category, VALUATION_FIGURES[1]) for category in CATEGORIES]
        for name in ("key_facts", "custom_fields", "summary")
    }
    brief = c.Brief(
        stage=fact("brief:stage", "status"),
        alive=fact("brief:alive", "status"),
        case_value=fact("brief:case_value", "valuation", VALUATION_FIGURES[0]),
        coverage=fact("brief:coverage", "valuation", VALUATION_FIGURES[2]),
        coverage_signal=c.CoverageSignal(band="confirmed", display="synthetic band text", sources=[ref("band")]),
        firm_spend=fact("brief:firm_spend", "internal", VALUATION_FIGURES[1]),
        last_client_contact=fact("brief:last_client_contact", "internal"),
        limitations=fact("brief:limitations", "internal"),
        summary=firm_lists["summary"],
    )
    claims = [
        c.Claim(
            id=f"claim:{category}",
            text=mark(category, "none", "ai", "claim.text"),
            topic=mark(category, "none", "ai", "claim.topic"),
            origin="notes",
            derivation="ai",
            category=category,
            source=ref("claim"),
        )
        for category in CATEGORIES
    ]
    nodes = [
        c.ValueNode(
            id=kind,
            kind=kind,
            label=mark("valuation", "firm", "clio", f"node.{kind}.label"),
            amount=VALUATION_FIGURES[0],
            derivation="computed",
            sources=[ref("node")],
        )
        for kind in ("value", "coverage", "gate", "lien", "cost", "fee", "net")
    ]
    agenda = c.Agenda(
        as_of=DAY,
        waiting=[
            c.AgendaItem(
                id="task:1",
                kind="task",
                title=mark("internal", "firm", "clio", "agenda.title"),
                detail=mark("internal", "firm", "clio", "agenda.detail"),
                bucket="waiting",
                source=ref("agenda"),
            )
        ],
    )
    return c.CaseModel(
        meta=c.Meta(matter_id=MATTER_ID, generated_at=f"{DAY}T00:00:00Z"),
        matter=c.Matter(
            id=MATTER_ID,
            description=mark("internal", "firm", "clio", "matter.description"),
            client=contact(CLIENT, "client"),
            source=ref("matter"),
        ),
        brief=brief,
        key_facts=firm_lists["key_facts"],
        custom_fields=firm_lists["custom_fields"],
        contacts=[contact(CLIENT, "client"), contact(PROVIDER_A, "provider"), contact(PROVIDER_B, "provider"), contact(ADVERSE, "adverse")],
        nodes=nodes,
        claims=claims,
        conflicts=[
            c.Conflict(
                id="conflict:1",
                topic=mark("strategy", "firm", "ai", "conflict.topic"),
                summary=mark("strategy", "firm", "ai", "conflict.summary"),
                amount_at_stake=VALUATION_FIGURES[0],
            )
        ],
        agenda=agenda,
        timeline=timeline(),
        spend=c.Spend(
            total=VALUATION_FIGURES[1],
            lines=[c.ExpenseLine(clio_id=1, amount=VALUATION_FIGURES[1], note=mark("internal", "firm", "clio", "expense.note"), source=ref("expense"))],
        ),
        documents=[c.DocumentInfo(id=1, name=mark("internal", "firm", "clio", "document.name"), source=ref("document"))],
        providers=[panel(PROVIDER_A), panel(PROVIDER_B)],
    )


def every_allowlist() -> list[tuple[str, ...]]:
    return [combo for size in range(len(SHAREABLE) + 1) for combo in itertools.combinations(SHAREABLE, size)]


def permitted(category: str, owner: str, origin: str, field: str, viewer: int, allowed: set[str]) -> bool:
    """The rule the projection must implement, written independently of it."""
    if category not in allowed or category in FIRM_ONLY:
        return False
    if origin == "ai":
        return False  # model-written text reaches a provider only through an attorney's explicit approval
    if field in ("brief:stage.display", "brief:stage.label", "brief:alive.display", "brief:alive.label"):
        return True
    if field == "event.label":
        if category == "status":
            return owner == "none" or owner == str(viewer)
        if category == "attendance":
            return owner == str(viewer)
        return False
    if field.startswith("ask") and field.endswith(".text"):
        # With approvals the Clio task title never goes out; the approved wording is separate text.
        return owner == str(viewer) and not APPROVALS
    return False


def leaks(view: c.ProviderView, viewer: int, allowed: set[str]) -> list[str]:
    out = view.model_dump_json()
    problems = [
        f"{'|'.join(found)}"
        for found in MARK.findall(out)
        if not permitted(found[0], found[1], found[2], found[3], viewer, allowed)
    ]
    if "/api/matters/" in out:
        problems.append("an internal firm href (/api/matters/...) is in the view")
    other = next(p for p in PROVIDERS if p != viewer)
    data = json.dumps(json.loads(out))  # normalised number formatting
    for label, figure in (
        *[("a valuation figure", f) for f in VALUATION_FIGURES],
        ("another provider's page count", PAGES[other]),
        ("another provider's billed total", BILLED[other]),
        ("another provider's visit count", VISITS[other]),
    ):
        if repr(figure) in data or str(int(figure)) in data:
            problems.append(label)
    for own, category in ((PAGES[viewer], "records"), (BILLED[viewer], "bills"), (VISITS[viewer], "attendance")):
        if category not in allowed and str(int(own)) in data:
            problems.append(f"this provider's own {category} figure while {category} is off")
    if f"Synthetic contact {other}" in data or f"Synthetic contact {ADVERSE}" in data:
        problems.append("another party's name")
    return sorted(set(problems))


CASE = synthetic_case()
APPROVED = "synthetic approved wording"


def approvals_enforced() -> bool:
    """Whether this build sends a request only in wording the attorney approved
    (`SharePolicy.approved_asks`). Asked of the code, not of the contract: the field can exist before the rule does."""
    if "approved_asks" not in c.SharePolicy.model_fields:
        return False
    bare = c.SharePolicy(contact_id=PROVIDER_A, allowed_categories=SHAREABLE)
    return share.provider_view(CASE, PROVIDER_A, bare).asks == []


APPROVALS = approvals_enforced()


def policy_for(viewer: int, allowed: list[str], hidden: list[str] | None = None) -> c.SharePolicy:
    """A policy as an attorney would leave it: categories chosen and, where the build asks for it, each request's wording approved."""
    extra = {}
    if APPROVALS:
        asks = next(p for p in CASE.providers if p.contact.id == viewer).asks
        extra["approved_asks"] = {ask.id: APPROVED for ask in asks}
    return c.SharePolicy(contact_id=viewer, allowed_categories=allowed, hidden_item_ids=hidden or [], **extra)


@pytest.mark.parametrize("viewer", PROVIDERS)
def test_no_allowlist_lets_anything_else_through(viewer: int) -> None:
    failures = {}
    for combo in every_allowlist():
        found = leaks(share.provider_view(CASE, viewer, policy_for(viewer, list(combo))), viewer, set(combo))
        if found:
            failures[combo] = found
    assert not failures, "leaks, by allowlist:\n" + "\n".join(f"  {list(k)}: {v}" for k, v in list(failures.items())[:6])


@pytest.mark.parametrize("viewer", PROVIDERS)
def test_firm_only_categories_are_inert_even_if_a_policy_names_them(viewer: int) -> None:
    """Defence in depth: a policy row that somehow lists a firm-only category shares nothing extra."""
    policy = c.SharePolicy.model_construct(
        contact_id=viewer, allowed_categories=SHAREABLE + FIRM_ONLY, hidden_item_ids=[], message=None, updated_at=None
    )
    view = share.provider_view(CASE, viewer, policy)
    assert not set(view.shared_categories) & set(FIRM_ONLY)
    assert not leaks(view, viewer, set(SHAREABLE))


def test_saving_a_policy_with_a_firm_only_category_is_refused(tmp_path) -> None:
    conn = connect(tmp_path / "synthetic.db")
    for category in FIRM_ONLY:
        with pytest.raises(share.NotShareable):
            share.save_policy(conn, MATTER_ID, c.SharePolicy(contact_id=PROVIDER_A, allowed_categories=["status", category]))


@pytest.mark.parametrize("viewer", PROVIDERS)
def test_empty_allowlist_shares_nothing_but_identity(viewer: int) -> None:
    view = share.provider_view(CASE, viewer, c.SharePolicy(contact_id=viewer))
    assert view.stage is None and view.alive is None and view.records is None and view.bills is None
    assert view.attendance is None and view.asks == [] and view.updates == []
    assert view.coverage.shared is False and view.coverage.band is None
    assert not leaks(view, viewer, set())


@pytest.mark.parametrize("viewer", PROVIDERS)
def test_outbound_view_carries_no_firm_plumbing(viewer: int) -> None:
    view = share.provider_view(CASE, viewer, policy_for(viewer, SHAREABLE))
    assert view.withheld_counts == {} and view.warnings == [], "preview-only fields on an outbound view"
    for item in (view.stage, view.alive):
        assert item.sources == [] and item.conflict_ids == [] and item.detail is None and item.amount is None
    for event in view.updates:
        assert event.sources == [] and event.claim_ids == [] and event.node_deltas == {}
    for block in (view.records, view.bills, view.attendance, view.coverage, *view.asks):
        assert block.sources == []
    assert all(ask.contact_id == viewer for ask in view.asks)


@pytest.mark.parametrize("viewer", PROVIDERS)
def test_items_the_attorney_removed_stay_out(viewer: int) -> None:
    full = share.provider_view(CASE, viewer, policy_for(viewer, SHAREABLE))
    removed = [ask.id for ask in full.asks] + [event.id for event in full.updates]
    assert full.asks and full.updates, "the synthetic case should produce removable items of both kinds"
    view = share.provider_view(CASE, viewer, policy_for(viewer, SHAREABLE, hidden=removed))
    assert view.asks == [] and view.updates == []


def test_asking_for_a_contact_who_is_not_a_provider_fails() -> None:
    for contact_id in (CLIENT, ADVERSE, 999):
        with pytest.raises(LookupError):
            share.provider_view(CASE, contact_id, c.SharePolicy(contact_id=contact_id, allowed_categories=SHAREABLE))


# -- the frozen snapshot --------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "synthetic.db")
    yield connection
    connection.close()


def send(conn, viewer: int, allowed: list[str]):
    policy = policy_for(viewer, allowed)
    builder = SimpleNamespace(matter_id=MATTER_ID, policy=lambda contact_id: policy)
    entry = share.send(conn, builder, CASE, viewer)
    row = conn.execute("SELECT * FROM shares WHERE id=?", (int(entry.id),)).fetchone()
    return entry, row


@pytest.mark.parametrize("viewer", PROVIDERS)
@pytest.mark.parametrize("allowed", [SHAREABLE, ["status"], ["bills", "records"], []])
def test_snapshot_obeys_the_same_rule_as_the_preview(conn, viewer: int, allowed: list[str]) -> None:
    _, row = send(conn, viewer, allowed)
    frozen = share.snapshot(conn, row)
    assert not leaks(frozen, viewer, set(allowed))
    # Everything stored for the share, not only what is served back.
    stored = " ".join(str(row[key]) for key in row.keys() if key != "token")
    stray = [m for m in MARK.findall(stored) if not permitted(m[0], m[1], m[2], m[3], viewer, set(allowed))]
    assert not stray, f"the share row itself holds: {stray[:5]}"
    assert frozen.withheld_counts == {} and frozen.warnings == []


def test_snapshot_is_what_the_preview_showed(conn) -> None:
    previewed = share.provider_view(CASE, PROVIDER_A, policy_for(PROVIDER_A, SHAREABLE), preview=True)
    _, row = send(conn, PROVIDER_A, SHAREABLE)
    frozen = share.snapshot(conn, row)
    ignore = {"generated_at", "expires_at", "withheld_counts", "warnings"}
    assert previewed.model_dump(exclude=ignore) == frozen.model_dump(exclude=ignore)


def test_snapshot_does_not_follow_the_case_after_sending(conn) -> None:
    _, row = send(conn, PROVIDER_A, SHAREABLE)
    before = share.snapshot(conn, row).model_dump_json()
    changed = CASE.model_copy(deep=True)
    changed.brief.stage.display = "a later stage"
    changed.providers[0].asks.clear()
    assert share.snapshot(conn, row).model_dump_json() == before


def test_one_providers_link_never_serves_the_others_reply(conn) -> None:
    _, row_a = send(conn, PROVIDER_A, SHAREABLE)
    _, row_b = send(conn, PROVIDER_B, SHAREABLE)
    ask_a = share.snapshot(conn, row_a).asks[0].id
    share.reply_to_ask(conn, row_a, ask_a, "synthetic reply from A")
    assert "synthetic reply from A" in share.snapshot(conn, row_a).model_dump_json()
    assert "synthetic reply from A" not in share.snapshot(conn, row_b).model_dump_json()


def test_revoked_and_expired_links_are_dead(conn) -> None:
    entry, row = send(conn, PROVIDER_A, SHAREABLE)
    assert share.find_live(conn, row["token"]) is not None
    share.revoke(conn, MATTER_ID, int(entry.id))
    assert share.find_live(conn, row["token"]) is None
    _, second = send(conn, PROVIDER_A, SHAREABLE)
    conn.execute("UPDATE shares SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (second["id"],))
    conn.commit()
    assert share.find_live(conn, second["token"]) is None
    assert share.find_live(conn, "not-a-token") is None


# -- requests go out only in wording the attorney approved ---------------------------------------

needs_approvals = pytest.mark.skipif(not APPROVALS, reason="per-request approval is not enforced by this build")


@needs_approvals
@pytest.mark.parametrize("viewer", PROVIDERS)
def test_no_request_goes_out_unapproved(viewer: int) -> None:
    view = share.provider_view(CASE, viewer, c.SharePolicy(contact_id=viewer, allowed_categories=SHAREABLE))
    assert view.asks == [] and "asks" in view.shared_categories


@needs_approvals
@pytest.mark.parametrize("viewer", PROVIDERS)
def test_an_approved_request_goes_out_in_the_approved_wording_only(viewer: int) -> None:
    view = share.provider_view(CASE, viewer, policy_for(viewer, SHAREABLE))
    assert view.asks and all(ask.text == APPROVED for ask in view.asks)
    assert "ask0.text" not in view.model_dump_json(), "the Clio task title went out beside the approved wording"


@needs_approvals
def test_an_approval_for_another_providers_request_or_a_stale_one_sends_nothing() -> None:
    theirs = [ask.id for ask in CASE.providers[1].asks]
    approvals = {ask_id: APPROVED for ask_id in theirs} | {"ask:gone": APPROVED}
    policy = c.SharePolicy(contact_id=PROVIDER_A, allowed_categories=SHAREABLE, approved_asks=approvals)
    assert share.provider_view(CASE, PROVIDER_A, policy).asks == []
