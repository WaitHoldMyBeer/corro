"""A saved assistant document as a PDF, laid out in code with PyMuPDF.

Rendered on request from our own store and never written into the matter. The
body is a table whose header row is drawn again at the top of every page; each
row's source cell carries reference numbers, and the references are listed in
full at the end (document, page, quote). A document's id is a hash of its
content, so a rendered PDF is kept in memory under that id and never goes stale.
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from html import escape

from shared import assistant_contract as a

PAGE = "letter"
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 40.0, 44.0, 46.0
HEADER_ROOM = 40.0  # space offered to the header row; it takes what its text needs
ROW_GAP = 3.0
QUOTE_CHARS = 320
REFS_PER_CELL = 12
NOTICE = "Created by the assistant. Firm-only draft, not checked by a lawyer."

CSS = (
    "body{font-family:sans-serif;font-size:8.5pt;line-height:1.25;color:#111}"
    "h1{font-size:16pt;margin:0 0 4pt 0}h2{font-size:11pt;margin:10pt 0 4pt 0}"
    "p{margin:0 0 3pt 0}.meta{color:#444}.notice{font-weight:bold}"
    ".th{font-weight:bold;font-size:8pt}.small{font-size:7.5pt;color:#333}.ref{font-size:7.5pt;margin:0 0 3pt 0}"
)
# Share of the table's width each column takes, by key; a column not named here takes DEFAULT_SHARE.
SHARES = {"date": 11.0, "provider": 20.0, "what": 44.0, "source": 25.0, "amount": 12.0, "group": 18.0}
DEFAULT_SHARE = 16.0

_cache: OrderedDict[tuple[int, str], tuple[bytes, int]] = OrderedDict()
_cache_lock = threading.Lock()
CACHE_DOCUMENTS = 24


def _money(amount: float | None) -> str:
    return "" if amount is None else f"${amount:,.2f}"


def _cell(entry: a.DocumentEntry, key: str, numbers: dict[str, int]) -> str:
    """One cell of one row, as HTML. Everything from the file is escaped."""
    if key == "date":
        return escape(entry.date or "")
    if key == "provider":
        printed = f'<br/><span class="small">as printed: {escape(entry.provider_as_printed)}</span>' if entry.provider_as_printed else ""
        return escape(entry.provider or "") + printed
    if key == "what":
        return escape(entry.what)
    if key == "amount":
        return escape(_money(entry.amount))
    if key == "group":
        return escape(entry.group or "")
    if key == "source":
        # A stored file name has no spaces to wrap at; underscores become spaces so it breaks inside its column.
        where = escape((entry.source_label or "").replace("_", " ").replace("  ", " ")) + (f", p. {entry.page}" if entry.page else "")
        refs = " ".join(f"[{numbers[ref]}]" for ref in entry.cite[:REFS_PER_CELL] if ref in numbers)
        more = f" +{len(entry.cite) - REFS_PER_CELL}" if len(entry.cite) > REFS_PER_CELL else ""
        return (where + ("<br/>" if where and refs else "") + f'<span class="small">{refs}{more}</span>').strip()
    return ""


def render(saved: a.SavedDocument, matter_line: str) -> tuple[bytes, int]:
    """(PDF bytes, page count) for one saved document."""
    import pymupdf

    document, citations = saved.document, saved.citations
    numbers: dict[str, int] = {}
    for entry in document.entries:
        for ref in entry.cite:
            if ref in citations and ref not in numbers:
                numbers[ref] = len(numbers) + 1

    sheet = pymupdf.paper_rect(PAGE)
    left, right = MARGIN_X, sheet.width - MARGIN_X
    top, bottom = MARGIN_TOP, sheet.height - MARGIN_BOTTOM
    columns = document.columns or [a.DocumentColumn(key="what", label="Entry"), a.DocumentColumn(key="source", label="Source")]
    shares = [SHARES.get(column.key, DEFAULT_SHARE) for column in columns]
    edges, at = [], left
    for share in shares:
        width = (right - left) * share / sum(shares)
        edges.append((at, at + width))
        at += width

    def story(html: str):
        return pymupdf.Story(html=f"<body>{html}</body>", user_css=CSS)

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    rules: list[tuple[int, float, float]] = []  # (page index, y, line width), drawn once the pages exist
    state = {"page": -1, "device": None, "y": top}

    def new_page() -> None:
        if state["device"] is not None:
            writer.end_page()
        state["device"] = writer.begin_page(sheet)
        state["page"] += 1
        state["y"] = top

    def table_header() -> None:
        """The header row, drawn at the top of the table on every page it runs onto."""
        lowest = state["y"]
        for (x0, x1), column in zip(edges, columns):
            head = story(f'<p class="th">{escape(column.label)}</p>')
            _, filled = head.place(pymupdf.Rect(x0 + 2, state["y"], x1 - 2, state["y"] + HEADER_ROOM))
            head.draw(state["device"])
            lowest = max(lowest, filled[3])
        state["y"] = lowest + 5
        rules.append((state["page"], state["y"] - 3, 0.9))

    def flow(html: str) -> None:
        """Running text, continued over as many pages as it needs."""
        text = story(html)
        more = 1
        while more:
            more, filled = text.place(pymupdf.Rect(left, state["y"], right, bottom))
            text.draw(state["device"])
            state["y"] = filled[3] + 4
            if more:
                new_page()

    new_page()
    totals = document.totals
    counted = f"{totals.entries} entries, {len(numbers)} references" + (f", {totals.undated} undated statements left out" if totals.undated else "")
    flow(
        f"<h1>{escape(document.title)}</h1>"
        f'<p class="meta">{escape(matter_line)}</p>'
        f'<p class="notice">{escape(NOTICE)}</p>'
        f'<p class="meta">Built {escape(document.created_at)} from the stored file (version {escape(document.ledger_version)}). {escape(counted)}.'
        + (f" Total of the amounts that add up: {escape(_money(totals.amount))}." if totals.amount is not None else "")
        + " Every row cites the pages it was read from; the numbered references are listed at the end.</p>"
    )
    state["y"] += 4
    table_header()
    tall = 10_000.0
    for entry in document.entries:
        cells = [story(_cell(entry, column.key, numbers) or "&#160;") for column in columns]
        heights = []
        for (x0, x1), cell in zip(edges, cells):
            _, filled = cell.place(pymupdf.Rect(x0 + 2, 0, x1 - 2, tall))
            heights.append(filled[3])
        height = min(max(heights), bottom - top - HEADER_ROOM)
        if state["y"] + height > bottom:
            new_page()
            table_header()
        for (x0, x1), cell in zip(edges, cells):
            cell.reset()
            cell.place(pymupdf.Rect(x0 + 2, state["y"], x1 - 2, state["y"] + height + 1))
            cell.draw(state["device"])
        state["y"] += height + ROW_GAP
        rules.append((state["page"], state["y"] - ROW_GAP / 2, 0.3))

    if numbers:
        listed = []
        for ref, number in numbers.items():
            citation = citations[ref]
            where = escape(citation.label) + (f", p. {citation.page}" if citation.page else "") + (f" ({escape(citation.date[:10])})" if citation.date else "")
            quote = (citation.quote or "").strip()
            if len(quote) > QUOTE_CHARS:
                quote = quote[:QUOTE_CHARS].rstrip() + " ..."
            checked = " [quote found in the page text]" if citation.quote_verified else (" [read from the page image]" if quote and citation.page else "")
            listed.append(f'<p class="ref">[{number}] {where}' + (f": &quot;{escape(quote)}&quot;{checked}" if quote else "") + "</p>")
        new_page()
        flow("<h2>References</h2>" + "".join(listed))

    writer.end_page()
    writer.close()

    pdf = pymupdf.open(stream=buffer.getvalue(), filetype="pdf")
    count = pdf.page_count
    for index, page in enumerate(pdf):
        for page_index, y, width in rules:
            if page_index == index:
                page.draw_line((left, y), (right, y), color=(0.45, 0.45, 0.45), width=width)
        page.insert_text((left, sheet.height - 24), f"{NOTICE}  {document.title}"[:150], fontsize=7, color=(0.35, 0.35, 0.35))
        page.insert_text((right - 60, sheet.height - 24), f"Page {index + 1} of {count}", fontsize=7, color=(0.35, 0.35, 0.35))
    pdf.set_metadata({"title": document.title, "subject": NOTICE, "creator": "assistant", "producer": "PyMuPDF"})
    out = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return out, count


def cached(matter_id: int, saved: a.SavedDocument, matter_line: str) -> tuple[bytes, int]:
    """The document's PDF, rendered once per document id and kept in memory."""
    key = (matter_id, saved.document.id)
    with _cache_lock:
        held = _cache.get(key)
        if held is not None:
            _cache.move_to_end(key)
            return held
    made = render(saved, matter_line)
    with _cache_lock:
        _cache[key] = made
        while len(_cache) > CACHE_DOCUMENTS:
            _cache.popitem(last=False)
    return made


def forget(matter_id: int, document_id: str) -> None:
    with _cache_lock:
        _cache.pop((matter_id, document_id), None)
