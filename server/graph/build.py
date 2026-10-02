"""Builds the graph payload for one matter from what is stored: the Clio items as
synced and the digest's claims. No model call and no request to Clio happens here.

Every label, link and position is derived from the stored rows at build time.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from typing import Any

from .. import review_queue
from ..case import CaseBuilder, source_href
from ..config import Settings
from ..db import items
from ..digest import pipeline, prompts
from . import layout, text

FORMAT = 1
BUILD_VERSION = "g7"  # bump when nodes, layout or scoring change, so stored graphs are rebuilt

EDGE_KINDS = ("contains", "party", "related", "group")
CONTAINS, PARTY, RELATED, GROUP = range(4)

# Clusters of the same family share a sector of the picture.
FAMILY_DOCUMENTS, FAMILY_PEOPLE, FAMILY_NOTES, FAMILY_SCHEDULE, FAMILY_FIELDS = range(5)
FAMILIES = 5

# Record kinds with no container in Clio get a hub so they read as a group: (label, family).
GROUPS = {
    "communication": ("Communications", FAMILY_PEOPLE),
    "note": ("Notes", FAMILY_NOTES),
    "task": ("Tasks", FAMILY_SCHEDULE),
    "calendar_entry": ("Calendar", FAMILY_SCHEDULE),
    "custom_field": ("Fields", FAMILY_FIELDS),
    "expense": ("Expenses", FAMILY_FIELDS),
}

RADIUS = {"matter": 20.0, "folder": 13.0, "group": 13.0, "contact": 12.0}
ITEM_RADIUS, ITEM_RADIUS_SPAN = 6.0, 5.0
CLAIM_LAYOUT = {"r": 3.5, "gap": 8, "step": 5}
BOUNDS_PAD = 60.0
UPLOAD_ROOM = 12  # room held open for documents added outside Clio: up to this many arrive without anything moving

# A file name is not a title. Words of one to three letters outside this list are read as initials.
SHORT_WORDS = frozenset(
    "a an and as at by for in of on or the to vs per no new old all pay fee due day key log out off own one two six ten doc"
    " mr ms dr st co inc ltd llc esq re bill law car tax job set raw top use end ex not its his her our you who how why"
    " was are has had can may did get got let put run saw say see sue son act age aid air arm art bar bed box buy cab"
    " cut eye far few fit gas hip hit ice job leg lot low man map men met mix net nor now odd oil pad pen pin rib row"
    " sit ski sum sun tab tip toe van war way web wet win yes yet".split()
)
FILE_TYPES = frozenset("pdf doc docx txt rtf jpg jpeg png tif tiff heic xls xlsx csv msg eml".split())
TITLE, BODY, STATEMENT = 3.0, 1.0, 2.0


def version(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> str:
    """Changes when anything the graph is built from changes: a Clio item, a record's
    claims, or a page read. Cheap enough to compute on every request."""
    digest = hashlib.sha256()

    def feed(*parts: Any) -> None:
        digest.update(("\x1f".join("" if part is None else str(part) for part in parts) + "\x1e").encode())

    feed(BUILD_VERSION, prompts.PAGES_VERSION, cfg.digest_model_bulk, *review_queue.stamp(conn, matter_id))
    for row in conn.execute(
        "SELECT kind, clio_id, content_hash FROM clio_items WHERE matter_id=? AND removed_at IS NULL ORDER BY kind, clio_id",
        (matter_id,),
    ):
        feed(*row)
    for row in conn.execute(
        "SELECT kind, clio_id, content_hash, prompt_version, model, at FROM item_claims WHERE matter_id=? ORDER BY kind, clio_id",
        (matter_id,),
    ):
        feed(*row)
    for row in conn.execute(
        "SELECT b.document_id, b.sha256, COUNT(p.page), MAX(p.at) FROM document_blobs b"
        " LEFT JOIN page_reads p ON p.sha256=b.sha256 AND p.prompt_version=? AND p.model=?"
        " WHERE b.matter_id=? GROUP BY b.document_id ORDER BY b.document_id",
        (prompts.PAGES_VERSION, cfg.digest_model_bulk, matter_id),
    ):
        feed(*row)
    return digest.hexdigest()[:20]


def readable(name: str) -> tuple[str, str | None]:
    """(title, reference) from a file name: the last `__` part in words, and any earlier part
    that carries a number, such as a docket reference. No part is interpreted beyond that."""
    stem = name.strip()
    if "." in stem and stem.rsplit(".", 1)[1].lower() in FILE_TYPES:
        stem = stem.rsplit(".", 1)[0]
    parts = [part for part in stem.split("__") if part.strip("-_ ")]
    if not parts:
        return name, None

    def words(part: str) -> str:
        out = []
        for word in re.split(r"[-_\s]+", part.strip("-_ ")):
            if not word:
                continue
            if word.isalpha() and word.islower():
                word = word.upper() if len(word) <= 3 and word not in SHORT_WORDS else word.capitalize()
            out.append(word)
        return " ".join(out)

    reference = next((words(part) for part in parts[1:-1] if any(ch.isdigit() for ch in part)), None)
    return words(parts[-1]) or name, reference


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


def _name(value: Any) -> str:
    return (value or {}).get("name") or "" if isinstance(value, dict) else ""


class _Graph:
    def __init__(self, matter_id: int):
        self.matter_id = matter_id
        self.nodes: list[dict[str, Any]] = []
        self.fields: list[list[tuple[float, str]]] = []  # weighted search text per node
        self.sort: list[tuple] = []  # order inside a cluster
        self.by_id: dict[str, int] = {}
        self.edges: set[tuple[int, int, int]] = set()

    def add(
        self,
        kind: str,
        clio_id: Any,
        label: str | None,
        *,
        sub: str | None = None,
        date: Any = None,
        body: tuple[Any, ...] = (),
        linked: bool = True,
        order: tuple = (),
    ) -> int:
        label = (label or kind.replace("_", " ")).strip()
        node = {
            "id": f"{kind}:{clio_id}",
            "kind": kind,
            "clio_id": clio_id if linked else None,
            "label": label[:140],
            "sub": (sub or None) and str(sub).strip()[:100],
            "date": _day(date),
            "href": source_href(self.matter_id, kind, clio_id) if linked else None,
        }
        self.by_id[node["id"]] = len(self.nodes)
        self.nodes.append(node)
        searchable = [(TITLE, label)] + [(BODY, str(part)) for part in (sub, node["date"], *body) if part not in (None, "")]
        self.fields.append(searchable if kind != "group" else [])
        self.sort.append(order or (node["date"] or "", label))
        return len(self.nodes) - 1

    def link(self, a: int, b: int, kind: int) -> None:
        if a != b:
            self.edges.add((a, b, kind))


def build(cfg: Settings, conn: sqlite3.Connection, case: CaseBuilder) -> dict[str, Any]:
    matter_id = case.matter_id
    graph = _Graph(matter_id)
    clusters: list[layout.Cluster] = []
    matter = case.matter

    root = graph.add(
        "matter",
        matter["id"],
        matter.get("display_number") or matter.get("description"),
        sub=matter.get("description"),
        date=matter.get("open_date"),
        body=(matter.get("status"), _name(matter.get("practice_area")), _name(matter.get("matter_stage")),
              _name(matter.get("client")), _name(matter.get("responsible_attorney"))),
    )

    # -- folders and documents: Clio's own containment
    folders = items(conn, matter_id, "folder")
    folder_node: dict[int, int] = {}
    folder_cluster: dict[int, layout.Cluster] = {}
    for folder in sorted(folders, key=lambda row: (row.get("name") or "", row["id"])):
        index = graph.add("folder", folder["id"], folder.get("name"), linked=False)
        folder_node[folder["id"]] = index
        folder_cluster[folder["id"]] = layout.Cluster(f"folder:{folder['id']}", FAMILY_DOCUMENTS, index)
    for folder in folders:
        parent = (folder.get("parent") or {}).get("id")
        graph.link(folder_node.get(parent, root), folder_node[folder["id"]], CONTAINS)
    pages = {
        row["document_id"]: row["page_count"] or 0
        for row in conn.execute("SELECT document_id, page_count FROM document_blobs WHERE matter_id=?", (matter_id,))
    }
    page_words: dict[int, list[str]] = {}
    for document_id, _page, read in pipeline.page_reads(cfg, conn, matter_id):
        seen = page_words.setdefault(document_id, [])
        for value in (read.get("title"), read.get("issuer"), (read.get("page_type") or "").replace("_", " ")):
            if value and value not in seen:
                seen.append(value)
    loose = None  # documents with no folder at all
    named: dict[str, layout.Cluster] = {}  # folders known only by name: a case created here has no folder records
    # Documents uploaded after the case existed get room of their own, placed after everything
    # else and held open even while empty, so one arriving moves nothing already on screen.
    added = layout.Cluster("group:uploaded", FAMILY_DOCUMENTS, -1, room=UPLOAD_ROOM)
    # An upload is told by its origin, not by its id: in a case created in our own store every id is local.
    # Where every document is an upload there is nothing for one to be "added" to, and all are laid out as the file.
    late = any(document.get("origin") != "uploaded" for document in case.documents)
    for document in case.documents:
        parent = (document.get("parent") or {}).get("id")
        name = document.get("name") or document.get("filename") or ""
        title, reference = readable(name)
        uploaded = late and document.get("origin") == "uploaded"
        index = graph.add(
            "document",
            document["id"],
            title,
            sub=" · ".join(part for part in (_name(document.get("parent")), reference) if part),
            date=document.get("received_at") or document.get("created_at"),
            # `search_text` is the text layer an import stored when no page of the document has been read yet
            body=(name, *page_words.get(document["id"], ()), document.get("search_text")),
            order=(document.get("created_at") or "", -document["id"]) if uploaded else (),
        )
        graph.nodes[index]["pages"] = pages.get(document["id"], 0)
        graph.nodes[index]["name"] = name
        if uploaded:
            graph.nodes[index]["uploaded"] = True
            if added.hub < 0:
                added.hub = graph.add("group", "uploaded", "Added to the file", linked=False)
                graph.link(root, added.hub, GROUP)
            added.members.append(index)
            graph.link(added.hub, index, GROUP)
            if parent in folder_node:
                graph.link(folder_node[parent], index, CONTAINS)
        elif parent in folder_cluster:
            folder_cluster[parent].members.append(index)
            graph.link(folder_node[parent], index, CONTAINS)
        elif _name(document.get("parent")):
            label = _name(document.get("parent"))
            if label not in named:
                hub = graph.add("folder", f"name:{label}", label, linked=False)
                named[label] = layout.Cluster(f"folder:name:{label}", FAMILY_DOCUMENTS, hub)
                graph.link(root, hub, CONTAINS)
            named[label].members.append(index)
            graph.link(named[label].hub, index, CONTAINS)
        else:
            if loose is None:
                hub = graph.add("group", "document", "Documents", linked=False)
                loose = layout.Cluster("group:document", FAMILY_DOCUMENTS, hub)
                graph.link(root, hub, GROUP)
            loose.members.append(index)
            graph.link(loose.hub, index, GROUP)
    clusters += list(folder_cluster.values()) + [named[label] for label in sorted(named)] + ([loose] if loose else [])

    # -- contacts, and each communication beside the first outside party on it
    related = {(row.get("contact") or {}).get("id") for row in case.relationships} | {case.client_id}
    contact_cluster: dict[int, layout.Cluster] = {}
    for contact in case.contacts:
        raw = next((row for row in case.contacts_raw if row["id"] == contact.id), {})
        index = graph.add(
            "contact",
            contact.id,
            contact.name,
            sub=contact.role_text or contact.role,
            body=(contact.role, raw.get("title"), _name(raw.get("company")), contact.email),
        )
        contact_cluster[contact.id] = layout.Cluster(f"contact:{contact.id}", FAMILY_PEOPLE, index)
        graph.link(root, index, RELATED if contact.id in related else GROUP)

    groups: dict[str, layout.Cluster] = {}

    def group(kind: str) -> layout.Cluster:
        if kind not in groups:
            label, family = GROUPS[kind]
            hub = graph.add("group", kind, label, linked=False)
            groups[kind] = layout.Cluster(f"group:{kind}", family, hub)
            graph.link(root, hub, GROUP)
        return groups[kind]

    def grouped(kind: str, index: int) -> None:
        cluster = group(kind)
        cluster.members.append(index)
        graph.link(cluster.hub, index, GROUP)

    for row in case.communications:
        people = (row.get("senders") or []) + (row.get("receivers") or [])
        index = graph.add(
            "communication",
            row["id"],
            row.get("subject"),
            sub={"EmailCommunication": "Email", "PhoneCommunication": "Phone call"}.get(row.get("type") or ""),
            date=row.get("date"),
            body=(row.get("body"), *(person.get("name") for person in people)),
        )
        outside = [p["id"] for p in people if p.get("type") != "User" and p.get("id") in contact_cluster]
        for contact_id in outside:
            graph.link(contact_cluster[contact_id].hub, index, PARTY)
        if outside:
            contact_cluster[outside[0]].members.append(index)
        else:
            grouped("communication", index)
    clusters += list(contact_cluster.values())

    # -- the firm's own records
    for row in case.notes:
        index = graph.add("note", row["id"], row.get("subject"), sub=_name(row.get("author")), date=row.get("date"),
                          body=(row.get("detail"),))
        grouped("note", index)
    for row in case.tasks:
        index = graph.add("task", row["id"], row.get("name"), sub=row.get("status"), date=row.get("due_at"),
                          body=(row.get("description"), _name(row.get("assignee")), row.get("priority")))
        grouped("task", index)
    for row in case.calendar:
        index = graph.add("calendar_entry", row["id"], row.get("summary"), sub=row.get("location"), date=row.get("start_at"),
                          body=(row.get("description"),))
        grouped("calendar_entry", index)
    for value in matter.get("custom_field_values") or []:
        if value.get("value") in (None, ""):
            continue
        field_id = (value.get("custom_field") or {}).get("id") or value.get("id")
        index = graph.add("custom_field", field_id, value.get("field_name"), sub=str(value["value"]),
                          order=(value.get("field_display_order") or 0, value.get("field_name") or ""))
        grouped("custom_field", index)
    for row in case.expenses:
        amount = row.get("total")
        index = graph.add("expense", row["id"], row.get("note") or _name(row.get("expense_category")),
                          sub=f"${amount:,.2f}" if isinstance(amount, (int, float)) else None, date=row.get("date"),
                          body=(_name(row.get("expense_category")),))
        grouped("expense", index)
    clusters += list(groups.values())

    # -- claims: searchable, each hanging off the node it was read from
    claims: dict[str, list] = {"id": [], "node": [], "page": [], "text": [], "ok": []}
    claim_fields: list[list[tuple[float, str]]] = []
    per_node = [0] * len(graph.nodes)
    # a claim the lawyer retired in the review queue is out of the graph and out of the index
    for claim in review_queue.active(conn, matter_id, pipeline.collect_claims(cfg, conn, matter_id)):
        parent = graph.by_id.get(f"{claim['source_kind']}:{claim['clio_id']}")
        if parent is None or not claim.get("statement"):
            continue
        claims["id"].append(claim["id"])
        claims["node"].append(parent)
        claims["page"].append(claim.get("page") or 0)
        claims["text"].append(claim["statement"])
        claims["ok"].append(1 if claim.get("quote_verified") else 0)
        per_node[parent] += 1
        extra = (claim.get("quote"), claim.get("topic"), claim.get("party"), claim.get("issuer"), claim.get("date"),
                 (claim.get("kind") or "").replace("_", " "))
        claim_fields.append([(STATEMENT, claim["statement"])] + [(BODY, str(part)) for part in extra if part])

    # -- weights, positions
    # the scales come from what Clio holds, so a document added later cannot resize the rest
    held = [index for index, node in enumerate(graph.nodes) if not node.get("uploaded")]
    most_claims = max((per_node[index] for index in held), default=0) or 1
    most_pages = max((graph.nodes[index].get("pages", 0) for index in held), default=0) or 1
    for index, node in enumerate(graph.nodes):
        node["claims"] = per_node[index]
        if node["kind"] in RADIUS:
            node["w"], node["r"] = 1.0, RADIUS[node["kind"]]
            continue
        weight = math.log1p(per_node[index]) / math.log1p(most_claims)
        if node["kind"] == "document":
            weight = max(weight, math.log1p(node["pages"]) / math.log1p(most_pages))
        node["w"] = round(min(1.0, weight), 3)
        node["r"] = round(ITEM_RADIUS + ITEM_RADIUS_SPAN * node["w"], 1)
    for cluster in [*clusters, added]:
        cluster.members.sort(key=lambda member: graph.sort[member])
    where = layout.positions(clusters, FAMILIES, len(graph.nodes), late=[added])
    for node, (x, y) in zip(graph.nodes, where):
        node["x"], node["y"] = round(x, 1), round(y, 1)
    reach = [(n["x"] - n["r"], n["y"] - n["r"], n["x"] + n["r"], n["y"] + n["r"]) for n in graph.nodes]
    inner = added.radius - layout.CLUSTER_PAD  # the held-open room counts, filled or not, so the frame does not change either
    reach.append((added.x - inner, added.y - inner, added.x + inner, added.y + inner))
    bounds = [
        round(min(r[0] for r in reach) - BOUNDS_PAD, 1),
        round(min(r[1] for r in reach) - BOUNDS_PAD, 1),
        round(max(r[2] for r in reach) + BOUNDS_PAD, 1),
        round(max(r[3] for r in reach) + BOUNDS_PAD, 1),
    ]

    index = text.build_index(graph.fields + claim_fields)
    return {
        "v": FORMAT,
        "version": version(cfg, conn, matter_id),
        "matter_id": matter_id,
        "bounds": bounds,
        "nodes": graph.nodes,
        "edges": [list(edge) for edge in sorted(graph.edges)],
        "edge_kinds": list(EDGE_KINDS),
        "claims": claims,
        "claim_layout": CLAIM_LAYOUT,
        "index": index.wire(),
    }
