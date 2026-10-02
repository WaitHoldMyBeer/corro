"""The gallery's registered cards, read from the browser's own files.

The three manifests under `web/v2/cards/` import one module per card; each
module's default export carries the card's id, title and group. This reads
those, so the list of cards a designed dashboard may use is the list the
browser can actually render, with nothing typed here. When a request brings the
browser's registry with it, that is used instead.
"""

from __future__ import annotations

import re
from functools import lru_cache

from shared.card_spec import GalleryCard

from ..config import ROOT

CARDS_DIR = ROOT / "web" / "v2" / "cards"
IMPORT = re.compile(r"^import\s+\w+\s+from\s+'(\./[^']+\.js)';", re.MULTILINE)
FIELD = {name: re.compile(name + r":\s*'([^']+)'") for name in ("id", "title", "group")}


def _read(module) -> GalleryCard | None:
    try:
        text = module.read_text()
    except OSError:
        return None
    start = text.rfind("export default")
    exported = text[start:] if start >= 0 else text
    found = {}
    for name, pattern in FIELD.items():
        match = pattern.search(exported) or (pattern.search(text) if name == "group" else None)
        found[name] = match.group(1) if match else ""
    if not found["id"] or not found["title"]:
        return None
    lead = []
    for line in text.splitlines():
        if not line.startswith("//"):
            break
        lead.append(line.lstrip("/ ").strip())
    return GalleryCard(
        id=found["id"], title=found["title"], group=found["group"],
        template=bool(re.search(r"template:\s*true", exported)), about=" ".join(lead)[:200],
    )


@lru_cache(maxsize=1)
def gallery() -> tuple[GalleryCard, ...]:
    cards: dict[str, GalleryCard] = {}
    for manifest in sorted(CARDS_DIR.glob("*.manifest.js")):
        for path in IMPORT.findall(manifest.read_text()):
            card = _read((CARDS_DIR / path).resolve())
            if card is not None:
                cards.setdefault(card.id, card)
    return tuple(cards.values())
