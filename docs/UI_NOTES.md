# UI notes (2026-10-02)

For a no-build, no-Node setup: static HTML/CSS/ES modules in `web/`, served by FastAPI. Field names below are from `shared/contract.schema.json` (contract v0.1 at time of writing); re-check after later contract changes. Light/dark theming and print layouts are out of scope.

## 1. The value river: hand-written SVG, no library

**Recommendation: write the SVG by hand (about 60 lines, no dependency).**

Reasons:
- The graph is not a general flow network. It is a fixed formula, net = min(case value, reachable coverage) - liens - costs - fee, with a gate that holds amounts back. d3-sankey assumes conservation of flow (what enters a node leaves it) and lays nodes out to satisfy that. Our nodes do not conserve flow (the `min()`, the gate), so a generic layout would draw bands whose widths we would then have to override.
- d3-sankey is not standalone: its UMD file needs `d3-array` and `d3-shape` as globals, which means vendoring the full `d3.min.js` (about 280 KB) as well. That is two files, three licences' worth of reading, and an API (`nodeId`, `nodeAlign`, links as source/target/value) that we would feed with synthetic link values.
- The same amounts drive a hover, a click (open `ValueNode.sources`), a status style (`FactStatus`) and a gate marker; with hand SVG those are plain DOM nodes you control.
- Nothing to fail on conference Wi-Fi.

If a charting library is still preferred, d3-sankey, vendor exactly these (checked 2026-10-02, all returned HTTP 200):
- `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js` -> `web/vendor/d3.min.js` (about 280 KB; licence ISC, text at https://github.com/d3/d3/blob/main/LICENSE). Load first.
- `https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/dist/d3-sankey.min.js` -> `web/vendor/d3-sankey.min.js` (about 5.6 KB; licence BSD-3-Clause per its package.json, https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/package.json). Load second; it attaches to `window.d3`.
- Download with `curl -L -o web/vendor/<name> <url>` once, commit the files and keep the licence texts beside them in `web/vendor/LICENSES.md`.

### Minimal working layout (untested: there is no JS engine on this machine, so run it in a browser first)

Made-up data below; real data comes from `CaseModel.nodes` (`ValueNode`: `id, kind, label, amount, status, depends_on, sources`). The code does geometry only (scaling an amount to pixels). Totals and held-back amounts must come from the server; never subtract in the UI.

```js
// web/river.js
const SVG = 'http://www.w3.org/2000/svg';
const DEDUCT = new Set(['lien', 'cost', 'fee']);

function el(tag, attrs = {}, text) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;          // never innerHTML
  return e;
}

export function layoutRiver(nodes, { width, height, pad = 12, barW = 14, gap = 10, minH = 2 }) {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const depth = new Map();
  const d = n => {
    if (depth.has(n.id)) return depth.get(n.id);
    depth.set(n.id, 0);                              // cycle guard
    const deps = n.depends_on.filter(i => byId.has(i)).map(i => d(byId.get(i)));
    depth.set(n.id, deps.length ? 1 + Math.max(...deps) : 0);
    return depth.get(n.id);
  };
  nodes.forEach(d);
  const cols = [];
  for (const n of nodes) (cols[depth.get(n.id)] ??= []).push(n);
  cols.forEach(c => c.sort((a, b) => DEDUCT.has(a.kind) - DEDUCT.has(b.kind))); // main chain on top
  // one scale for the whole picture: the tallest column fills the height
  const amt = n => Math.max(n.amount ?? 0, 0);
  const need = cols.map(c => c.reduce((s, n) => s + amt(n), 0));
  const gaps = Math.max(...cols.map(c => (c.length - 1) * gap));
  const k = (height - 2 * pad - gaps) / (Math.max(...need) || 1);   // px per unit amount
  const pos = new Map();
  cols.forEach((c, i) => {
    const x = pad + (cols.length > 1 ? i * (width - 2 * pad - barW) / (cols.length - 1) : 0);
    let y = pad;
    for (const n of c) {
      const h = n.amount == null ? 12 : Math.max(amt(n) * k, minH);
      pos.set(n.id, { x, y, h, w: barW });
      y += h + gap;
    }
  });
  return pos;
}

export function renderRiver(host, nodes, onSelect) {
  const width = host.clientWidth, height = host.clientHeight;
  const pos = layoutRiver(nodes, { width, height });
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, width, height, role: 'img' });
  for (const n of nodes) {                            // ribbons first, bars on top
    const b = pos.get(n.id);
    for (const dep of n.depends_on) {
      const a = pos.get(dep);
      if (!a) continue;
      const t = Math.min(a.h, b.h);                   // thickness = smaller end
      const x0 = a.x + a.w, x1 = b.x, xm = (x0 + x1) / 2;
      svg.append(el('path', {
        class: `ribbon ${n.kind}`,
        d: `M${x0},${a.y} C${xm},${a.y} ${xm},${b.y} ${x1},${b.y} L${x1},${b.y + t}`
         + ` C${xm},${b.y + t} ${xm},${a.y + t} ${x0},${a.y + t} Z`,
      }));
    }
  }
  for (const n of nodes) {
    const b = pos.get(n.id);
    const g = el('g', { class: `bar ${n.kind} ${n.status}`, tabindex: 0 });
    g.append(el('rect', { x: b.x, y: b.y, width: b.w, height: b.h }));
    g.append(el('text', { x: b.x + b.w + 6, y: b.y + Math.min(b.h, 16) }, n.label));
    g.addEventListener('click', () => onSelect(n));
    g.addEventListener('keydown', e => e.key === 'Enter' && onSelect(n));
    svg.append(g);
  }
  host.replaceChildren(svg);
}
```

Usage with made-up nodes (replace with the API response):

```js
const demo = [
  { id: 'v', kind: 'value',    label: 'Value',    amount: 1000, status: 'assumed',   depends_on: [] },
  { id: 'c', kind: 'coverage', label: 'Coverage', amount: 600,  status: 'confirmed', depends_on: [] },
  { id: 'g', kind: 'gate',     label: 'Gate',     amount: 400,  status: 'contested', depends_on: ['v', 'c'] },
  { id: 'l', kind: 'lien',     label: 'Liens',    amount: 150,  status: 'confirmed', depends_on: ['g'] },
  { id: 'n', kind: 'net',      label: 'Net',      amount: 300,  status: 'assumed',   depends_on: ['g', 'l'] },
];
renderRiver(document.querySelector('#river'), demo, n => openSource(n.sources));
```

Check before relying on it:
- What `gate.amount` means (held-back amount vs. amount passed through) is not defined in the contract text I read. Confirm in the contract; the example assumes held-back. Likewise whether `net` depends on `gate` or directly on the deductions.
- Styling is CSS: `.bar.contested rect { stroke-dasharray: 4 3 }`, `.bar.unknown rect { fill: none }`, ribbons semi-transparent. Colour by `status`, not by kind, so the eye reads "what is firm".
- A null `amount` draws a 12 px stub (unknown), never a zero.
- No animation needed; if you add one, keep it to 600 ms on first paint only.

## 2. Vendored files

Vendor nothing for the river. Nothing else in this brief needs a library. If something is added, it goes under `web/vendor/` with its licence, and `index.html` must reference it by relative path; grep for `https://` in `web/` before the clean-clone check (expected hits: none).

## 3. Source drawer

The browser needs only the contract; no server work beyond what the server provides.
- On click of any source chip: `fetch(sourceRef.href)` returns a `SourceDetail`: `title, author, date, text, highlight, page_count, page_image_href, file_href, parties, ref{page, quote, quote_verified}`.
- Document source (`page_image_href` present): a `<dialog>` drawer with an `<img src="{page_image_href}" alt="Page {ref.page} of {title}">` at full drawer width, and beside or below it the verbatim quote in a `<blockquote>` with a "checked against the page" badge when `ref.quote_verified` is true and a muted "quote not verified" when false. Prev/next buttons change page by re-requesting the same href pattern only if the server provides a page parameter; otherwise show the single page and a link to `file_href#page=N` in a new tab. Highlighting the quote on the image is already on the cut list.
- Note or email source (`text` present, no image): a `<pre style="white-space: pre-wrap">` of `text`, with `highlight` marked by splitting the string around the first occurrence and wrapping the middle in `<mark>` using DOM nodes and `textContent`. Never `innerHTML`: email bodies are untrusted text.
- Native `<dialog>` with `showModal()` gives Esc to close, focus trapping and a backdrop for free. Style it as a right-hand panel (`margin-left: auto; height: 100vh; width: min(560px, 90vw)`).
- Large images: `loading="lazy"`, a fixed `max-height: 80vh` with `overflow: auto`, and show a skeleton text while loading. A failed image shows the quote and a "page image unavailable" line, never a broken icon.
- Client photo (`Brief.client_photo.image_href`) is the same kind of PNG; show initials if absent.

## 4. One-screen hierarchy at 1440 x 900 px

Design for about 1440 x 800 px of visible page (browser chrome takes the rest). The seven sections in this order; the drawer (7) is an overlay, not part of the layout. The ten-second read is: who, how much, is it safe, what is wrong, what is due.

| Zone | Content (section) | Height | Notes |
| --- | --- | --- | --- |
| A, top strip | 1. Header brief: photo, stage, case value, coverage, firm spend, last client contact | about 100 px (12%) | Six figures, large numerals, status colour on value and coverage only. Each one opens its source. |
| B, hero | 2. Value river (left, about 2/3 width) with 3. notes-say / document-shows cards (right, about 1/3, top two by `Conflict.severity`) | about 380 px (48%) | The river has the most area and the only saturated colour. Cards show topic and the two claims in two lines each; click opens the drawer. |
| C, fold peek | 4. Since you last opened, 6. overdue / coming / waiting, 5. what matters | the top 120 px (about 15%) visible, rest below the fold | Three columns, headings and first rows visible so the user sees there is more. The golden path says "scroll once". |
| Remainder | the rest of C | below the fold | At most ten timeline rows in "what matters". |

Rules:
- One hero (river), one accent colour, status as the only other colour, text at least 14 px (clip is shot at video resolution).
- Numbers first, labels small and above them; every number is a button.
- Show the digest badge (items digested, cost) small in the header strip.
- Empty states say what is missing ("no coverage fact found in the matter"), not "N/A".
- Provider portal (the other half) uses the same type scale and the narrow single column: stage, coverage band, asks, bills and records, reply box.

## 5. Out of scope

Dark mode, light/dark toggling, print stylesheets, mobile layout, i18n. Fixed desktop width of 1440 px is acceptable; test at 1440 x 900 and 1280 x 720.
