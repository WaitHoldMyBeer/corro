"""Document bytes on disk: file paths, page text and page images (PyMuPDF)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .config import Settings

RENDER_DPI = 110  # legible for a scanned letter page at drawer width; about 150-300 kB per PNG


def blob_path(cfg: Settings, conn: sqlite3.Connection, matter_id: int, document_id: int) -> Path | None:
    row = conn.execute(
        "SELECT path FROM document_blobs WHERE matter_id=? AND document_id=?", (matter_id, document_id)
    ).fetchone()
    if row is None:
        return None
    path = cfg.data_dir / row["path"]
    return path if path.exists() else None


def page_count(path: Path) -> int | None:
    import pymupdf as fitz

    try:
        with fitz.open(path) as document:
            return document.page_count
    except Exception:
        return None


MIN_TEXT_CHARS = 20  # fewer characters than this on a page is a stamp or a stray glyph, not a text layer


def text_layer_stats(path: Path) -> tuple[int | None, int | None]:
    """(pages, pages that carry a text layer). (None, None) if it is not a readable PDF."""
    import pymupdf as fitz

    try:
        with fitz.open(path) as document:
            with_text = sum(1 for page in document if len(page.get_text().strip()) >= MIN_TEXT_CHARS)
            return document.page_count, with_text
    except Exception:
        return None, None


def page_text(path: Path, page: int) -> str | None:
    """The text layer of a 1-based page; empty for a scan."""
    import pymupdf as fitz

    try:
        with fitz.open(path) as document:
            if not 1 <= page <= document.page_count:
                return None
            return document[page - 1].get_text()
    except Exception:
        return None


HIGHLIGHT_RGB = (1.0, 0.85, 0.2)
MAX_RENDER_PIXELS = 25_000_000  # about 5,000 x 5,000; a letter page at our resolution is under 2 million
MIN_FRAGMENT_WORDS = 3


def quote_boxes(sheet, quote: str) -> list:
    """Where a quote sits on a page, from the page's own text layer. The whole quote
    if the layer has it in one run; otherwise each line of it that can be found.
    A scan has no text layer, so nothing is returned and nothing is highlighted."""
    found = sheet.search_for(quote)
    if found:
        return found
    boxes = []
    for fragment in quote.splitlines():
        if len(fragment.split()) >= MIN_FRAGMENT_WORDS:
            boxes += sheet.search_for(fragment.strip())
    return boxes


def parse_crop(value: str | None) -> tuple[float, float, float, float] | None:
    """'left,top,right,bottom' as fractions of the page, or None if it is not a sane box."""
    try:
        left, top, right, bottom = (min(max(float(part), 0.0), 1.0) for part in (value or "").split(","))
    except ValueError:
        return None
    return (left, top, right, bottom) if right - left > 0.01 and bottom - top > 0.01 else None


def page_png(
    cfg: Settings,
    conn: sqlite3.Connection,
    matter_id: int,
    document_id: int,
    page: int,
    crop: tuple[float, float, float, float] | None = None,
    mark: str | None = None,
) -> Path | None:
    """Render a 1-based page (or a box within it) to PNG once and keep it under data/pages/.
    `mark` is a quote to highlight where the page's text layer contains it."""
    import pymupdf as fitz

    source = blob_path(cfg, conn, matter_id, document_id)
    if source is None or page < 1:
        return None
    suffix = "" if crop is None else "-" + "_".join(f"{value:.3f}" for value in crop)
    if mark:
        suffix += "-m" + hashlib.sha256(mark.encode()).hexdigest()[:12]
    target = (
        cfg.data_dir / "pages" / str(matter_id) / str(document_id) / f"{page}-{source.stat().st_mtime_ns}{suffix}.png"
    )
    if target.exists():
        return target
    try:
        with fitz.open(source) as document:
            if page > document.page_count:
                return None
            sheet = document[page - 1]
            clip = None
            if crop is not None:
                box = sheet.rect
                clip = fitz.Rect(
                    box.x0 + crop[0] * box.width, box.y0 + crop[1] * box.height,
                    box.x0 + crop[2] * box.width, box.y0 + crop[3] * box.height,
                )
            for box in quote_boxes(sheet, mark) if mark else []:
                # Drawn on the in-memory copy only; the stored file is never changed.
                sheet.draw_rect(box, color=None, fill=HIGHLIGHT_RGB, fill_opacity=0.35, overlay=True)
            dpi = RENDER_DPI if crop is None else RENDER_DPI * 2
            area = clip or sheet.rect
            pixels = (area.width * dpi / 72.0) * (area.height * dpi / 72.0)
            if pixels > MAX_RENDER_PIXELS:  # an enormous page is rendered smaller, not at any cost in memory
                dpi = max(int(dpi * (MAX_RENDER_PIXELS / pixels) ** 0.5), 8)
            pixmap = sheet.get_pixmap(dpi=dpi, clip=clip)
            target.parent.mkdir(parents=True, exist_ok=True)
            pixmap.save(target)
    except Exception:
        return None
    return target
