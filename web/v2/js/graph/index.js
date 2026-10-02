// The case-file graph: a search bar over a map of everything in the matter.
//
//   mountGraph(container, { matterId, api, openSource, compact })
//   mountGraph(container, caseModel, ctx, { setHeight })      // as the v2 shell calls it; setHeight means the dashboard hero
//
// `api(path)` returns the parsed JSON of GET /api/matters/{id}/graph and
// `openSource(ref)` opens a source reference in the shell's drawer. The module
// brings its own search input (the links start from it), legend, result list
// and tooltip, and loads its own stylesheet. Returns
//   { ready, setQuery(text), focusSearch(), update(), refresh(), stats(), results(), destroy(), ownsSearch: true,
//     highlightNodes(ids, { label }) / highlight(ids), focusNode(id), nodeAt(clientX, clientY) }
// where an id is a payload node id ("kind:clio id"). currentGraph() returns the handle on screen, if any.
// All text goes into the page through textContent: labels and statements are
// untrusted text from the matter.
import { buildModel } from './model.js';
import { createSearch } from './search.js';
import { createEngine, relColor, setRamp, spectrumCss } from './engine.js';
import { readPalette } from './palette.js';

const SVG = 'http://www.w3.org/2000/svg';
const CSS_HREF = new URL('../../css/graph.css', import.meta.url).href;
const REFRESH_MS = 3000;         // update() asks for a newer graph at most this often; the server answers 304 when there is none

// What the upload pill says for each stage the server reports while it reads a dropped file.
const STAGE_WORDS = { stored: 'saved', fingerprint: 'checking whether it is already in the file', read: 'reading the pages', reconcile: 'checking it against the file' };

let current = null;              // the handle on screen, for callers the shell did not hand one to
let carry = '';                  // a query on its way from the dashboard's map to the Graph tab
export function currentGraph() { return current; }

// The dashboard's map keeps one height, chosen from the viewport: nothing below it moves when a search starts.
// The same two steps as the shell's placeholder for it (.v2-hero.has-graph in shell.css), so nothing moves on load either.
const heroHeight = () => (window.innerHeight <= 800 ? 360 : 440);

function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid) e.append(kid);
  return e;
}

function searchIcon() {
  const s = document.createElementNS(SVG, 'svg');
  s.setAttribute('viewBox', '0 0 20 20'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('class', 'gx-icon');
  const c = document.createElementNS(SVG, 'circle');
  c.setAttribute('cx', '8.5'); c.setAttribute('cy', '8.5'); c.setAttribute('r', '5.5');
  const l = document.createElementNS(SVG, 'path');
  l.setAttribute('d', 'M12.8 12.8 17 17');
  s.append(c, l);
  return s;
}

function ensureCss() {
  if ([...document.styleSheets].some((s) => s.href === CSS_HREF) || document.querySelector(`link[href="${CSS_HREF}"]`)) return;
  document.head.append(el('link', { rel: 'stylesheet', href: CSS_HREF }));
}

const plural = (k, one, many) => `${k.toLocaleString('en-US')} ${k === 1 ? one : many}`;
const joined = (...parts) => parts.filter(Boolean).join(' · ');

export function mountGraph(container, a, b, c) {
  const shell = b && typeof b.api === 'function';                    // the shell's (caseModel, ctx, options) form
  const o = shell ? { matterId: b.matterId, api: b.api, openSource: b.openSource, compact: !!(c && c.setHeight), setHeight: c && c.setHeight } : (a || {});
  const { matterId, api, openSource, compact = false, data = null, setHeight = null } = o;
  const ROWS = compact ? 7 : 14;
  const ROW_PX = compact ? 78 : 95; // height of a result row, fixed in graph.css, so the list can end on a whole row
  const off = new AbortController();
  const on = (target, type, fn, extra) => target.addEventListener(type, fn, { signal: off.signal, ...extra });
  ensureCss();
  if (setHeight) setHeight(null);

  // ---- static DOM, built once ----
  const canvas = el('canvas', { class: 'gx-canvas', role: 'img', 'aria-label': 'Map of everything in the case file. The list beside it gives the same results as text.' });
  const input = el('input', { class: 'gx-input', type: 'search', placeholder: 'Search the whole case file', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Search the whole case file' });
  const count = el('span', { class: 'gx-count', 'aria-live': 'polite' });
  const bar = el('label', { class: 'gx-bar' }, searchIcon(), input, count);
  const legend = el('div', { class: 'gx-legend', role: 'group', 'aria-label': 'Kinds of item; press one to hide or show it' });
  const scaleBar = el('i');
  const scale = el('div', { class: 'gx-scale' }, el('span', { text: 'Link shade: weaker match' }), scaleBar, el('span', { text: 'stronger' }));
  const tipKind = el('span', { class: 'gx-tip-kind' }), tipTitle = el('strong', { class: 'gx-tip-title' });
  const tipPre = document.createTextNode(''), tipHit = el('mark'), tipPost = document.createTextNode('');
  const tipQuote = el('span', { class: 'gx-tip-quote' }, tipPre, tipHit, tipPost);
  const tipHint = el('span', { class: 'gx-tip-hint' });
  const tip = el('div', { class: 'gx-tip', role: 'tooltip', hidden: true }, tipKind, tipTitle, tipQuote, tipHint);
  const status = el('p', { class: 'gx-status', text: 'Drawing the map of the case file…' });
  const dropNote = el('p', { class: 'gx-drop', hidden: true, role: 'status' });
  const zoomBox = compact ? null : el('div', { class: 'gx-zoom' },
    el('button', { type: 'button', 'data-z': 'in', 'aria-label': 'Zoom in', text: '+' }),
    el('button', { type: 'button', 'data-z': 'out', 'aria-label': 'Zoom out', text: '−' }),
    el('button', { type: 'button', 'data-z': 'fit', text: 'Fit' }));
  const stage = el('div', { class: 'gx-stage' }, canvas, bar, legend, zoomBox, tip, dropNote, status);

  const heading = el('h3', { class: 'gx-side-h' }), sub = el('p', { class: 'gx-side-sub' });
  const list = el('ol', { class: 'gx-list' });
  const moreLink = compact ? el('a', { href: '#/graph' }) : null;
  const more = el('p', { class: 'gx-more', hidden: true }, moreLink);
  const side = el('aside', { class: 'gx-side', 'aria-label': 'Search results' }, heading, sub, list, more, scale);
  const rows = [];
  for (let i = 0; i < ROWS; i++) {
    const dot = el('span', { class: 'gx-dot' });
    const title = el('span', { class: 'gx-row-title' }), meta = el('span', { class: 'gx-row-meta' });
    const pre = document.createTextNode(''), hit = el('mark'), post = document.createTextNode('');
    const quote = el('span', { class: 'gx-row-quote' }, pre, hit, post);
    const relBar = el('i');
    const rel = el('span', { class: 'gx-rel', 'aria-hidden': 'true' }, relBar);
    const btn = el('button', { type: 'button', class: 'gx-row' }, dot, el('span', { class: 'gx-row-main' }, title, meta, quote, rel));
    const li = el('li', { hidden: true }, btn);
    list.append(li);
    rows.push({ li, btn, dot, title, meta, pre, hit, post, quote, rel, relBar, node: -1 });
  }

  const root = el('div', { class: `gx${compact ? ' gx-compact' : ''}` }, stage, side);
  const sizeHero = () => { root.style.height = `${heroHeight()}px`; };
  if (compact) sizeHero();
  container.replaceChildren(root);

  let model = null, search = null, engine = null, kindOn = null, chosen = -1, shown = 0, fetchedAt = 0, fitRows = ROWS, checking = false;
  // A set of nodes lit from outside (the assistant's citations) stands in for the search result until the box is typed in.
  let pinned = null, pinnedIds = null, pinnedLabel = '';
  const cur = () => pinned || search.res;

  // ---- what a click opens: the best matching statement while searching, else the item itself ----
  function claimFor(node, item) { return item >= 0 ? item : (cur().active ? cur().bestItem[node] : -1); }
  function open(node, item) {
    const ref = model.sourceRef(node, claimFor(node, item));
    if (ref && openSource) openSource(ref, model.labels[node]);
  }

  function setQuote(pre, hit, post, text, width, lead) {
    const s = text ? search.snippet(text, width) : null;
    pre.data = s ? (lead || '') + s.pre : ''; hit.textContent = s ? s.hit : ''; post.data = s ? s.post : '';
    return !!s;
  }
  const pageLead = (item) => (item >= 0 && model.ipages[item] ? `p. ${model.ipages[item]}: ` : '');

  function renderList() {
    const res = cur();
    const total = res.active ? res.nodeHits : model.n;
    shown = Math.min(fitRows, total);
    if (pinned) {
      heading.textContent = pinnedLabel || plural(total, 'item highlighted', 'items highlighted');
      sub.textContent = 'Lit on the map. Type in the search box to search instead.';
      count.textContent = '';
    } else if (res.active) {
      heading.textContent = total ? plural(total, 'item matches', 'items match') : 'Nothing in the file matches';
      sub.textContent = total ? `${plural(res.itemHits, 'matching statement', 'matching statements')} in them; strongest first` : 'Try another word, or fewer words.';
      count.textContent = `${total.toLocaleString('en-US')} · ${res.ms < 0.05 ? 'under 0.1' : (res.ms < 10 ? res.ms.toFixed(1) : Math.round(res.ms))} ms`;
    } else {
      heading.textContent = 'In this file';
      sub.textContent = `${plural(model.n, 'item', 'items')} and ${plural(model.m, 'statement', 'statements')} read from them; largest first`;
      count.textContent = '';
    }
    for (let r = 0; r < ROWS; r++) {
      const row = rows[r];
      if (r >= shown) { row.li.hidden = true; row.node = -1; continue; }
      const i = res.active ? res.nodeOrder[r] : model.byClaims[r];
      const kd = model.kinds[model.kind[i]];
      row.node = i; row.li.hidden = false;
      row.btn.setAttribute('data-node-id', model.ids[i]);
      row.dot.style.background = kd.color;
      row.title.textContent = model.labels[i];
      const best = res.active ? res.bestItem[i] : -1;
      const hits = res.active ? res.nodeCnt[i] : model.claimCount[i];
      row.meta.textContent = joined(model.kindName[i], model.dates[i], hits ? plural(hits, 'statement', 'statements') : '');
      row.quote.hidden = !setQuote(row.pre, row.hit, row.post, best >= 0 ? model.itexts[best] : model.subs[i], compact ? 84 : 120, pageLead(best));
      row.rel.hidden = !res.active || !!pinned;
      if (res.active && !pinned) { row.relBar.style.width = `${Math.max(4, Math.round(res.nodeRel[i] * 100))}%`; row.relBar.style.background = relColor(res.nodeRel[i]); }
      row.btn.classList.toggle('on', r === chosen);
    }
    more.hidden = total <= shown;
    if (total > shown) {
      if (moreLink) moreLink.textContent = `All ${total.toLocaleString('en-US')} in the Graph tab`;
      else more.textContent = `and ${(total - shown).toLocaleString('en-US')} more on the map`;
    }
  }

  function runQuery() {
    pinned = null; pinnedIds = null;
    search.run(input.value, kindOn);
    chosen = -1;
    engine.apply(search.res);
    renderList();
    hideTip();
  }

  // ---- tooltip ----
  function hideTip() { tip.hidden = true; canvas.style.cursor = ''; }
  function showTip() {
    const h = engine.hover();
    if (!h) { hideTip(); return; }
    const res = cur(), i = h.node;
    const kd = model.kinds[model.kind[i]];
    const item = claimFor(i, h.item);
    tipKind.textContent = h.item >= 0
      ? joined('Statement', model.ipages[item] ? `page ${model.ipages[item]}` : '')
      : joined(model.kindName[i], model.dates[i]);
    tipKind.style.setProperty('--gx-kind', kd.color);
    tipTitle.textContent = model.labels[i];
    tipQuote.hidden = !setQuote(tipPre, tipHit, tipPost, item >= 0 ? model.itexts[item] : model.subs[i], 200, h.item < 0 ? pageLead(item) : '');
    const linked = !!model.sourceRef(i);
    const n = res.active && h.item < 0 ? res.nodeCnt[i] : (h.item < 0 ? model.claimCount[i] : 0);
    tipHint.textContent = joined(n ? plural(n, res.active ? 'statement matches' : 'statement read from it', res.active ? 'statements match' : 'statements read from it') : '',
      linked ? (item >= 0 && model.ipages[item] ? 'Click to open the page' : 'Click to open the source') : '');
    tip.hidden = false;
    const p = h.item >= 0 ? engine.itemScreen(h.item) : engine.nodeScreen(i);
    const w = tip.offsetWidth, ht = tip.offsetHeight, sw = stage.clientWidth, sh = stage.clientHeight;
    let x = p.x + p.r + 14, y = p.y - ht / 2;
    if (x + w > sw - 8) x = p.x - p.r - 14 - w;
    if (x < 8) x = 8;
    y = Math.max(8, Math.min(sh - ht - 8, y));
    tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    canvas.style.cursor = linked ? 'pointer' : '';
  }

  // ---- size ----
  function measure() {
    if (!engine) return;
    const s = stage.getBoundingClientRect(), bb = bar.getBoundingClientRect();
    if (s.width < 2 || s.height < 2) return;
    engine.resize(s.width, s.height, window.devicePixelRatio || 1, bb.bottom - s.top + 18, legend.offsetHeight + 22);
    engine.setOrigin(bb.left - s.left + bb.width / 2, bb.bottom - s.top - 3, Math.max(0, bb.width / 2 - 26));
    dropNote.style.bottom = `${legend.offsetHeight + 20}px`;        // the upload pill sits above the legend, however many rows it wraps to
    list.style.maxHeight = '';                       // let the panel give the list its room, then trim it to whole rows
    const whole = Math.max(1, Math.floor(list.clientHeight / ROW_PX));
    list.style.maxHeight = `${whole * ROW_PX}px`;
    const fits = compact ? Math.min(ROWS, whole) : ROWS;
    if (fits !== fitRows) { fitRows = fits; if (search) renderList(); }
  }

  function wire() {
    const local = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let drag = null;
    on(canvas, 'pointermove', (e) => {
      const [x, y] = local(e);
      if (drag) {
        if (compact || (!drag.moved && Math.hypot(x - drag.x0, y - drag.y0) < 4)) return;
        drag.moved = true; hideTip(); canvas.style.cursor = 'grabbing';
        engine.panBy(x - drag.x, y - drag.y); drag.x = x; drag.y = y;
        return;
      }
      if (engine.pointAt(x, y)) showTip();
    });
    on(canvas, 'pointerdown', (e) => {
      if (e.button !== 0) return;
      const [x, y] = local(e);
      drag = { x, y, x0: x, y0: y, moved: false };
      if (!compact) canvas.setPointerCapture(e.pointerId);
    });
    on(canvas, 'pointerup', (e) => {
      const d = drag; drag = null;
      if (!d) return;
      if (d.moved) { canvas.style.cursor = ''; return; }
      const [x, y] = local(e);
      engine.pointAt(x, y);
      const h = engine.hover();
      if (h) open(h.node, h.item);
    });
    on(canvas, 'pointercancel', () => { drag = null; });
    on(canvas, 'pointerleave', () => { if (!drag) { engine.pointAt(-1e4, -1e4); hideTip(); } });
    if (!compact) {
      on(canvas, 'wheel', (e) => {
        e.preventDefault();
        const [x, y] = local(e);
        hideTip();
        engine.zoomAt(Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016)), x, y);
      }, { passive: false });
      on(zoomBox, 'click', (e) => {
        const z = e.target.getAttribute && e.target.getAttribute('data-z');
        if (z === 'fit') engine.resetView();
        else if (z) engine.zoomAt(z === 'in' ? 1.5 : 1 / 1.5, stage.clientWidth / 2, stage.clientHeight / 2);
      });
    }

    on(input, 'input', runQuery);
    on(input, 'keydown', (e) => {
      if (e.key === 'Escape' && (input.value || pinned)) { input.value = ''; runQuery(); e.preventDefault(); return; }
      if (e.key === 'Enter') { const r = rows[Math.max(0, chosen)]; if (shown && r.node >= 0) open(r.node, -1); return; }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault();
      if (!shown) return;
      chosen = e.key === 'ArrowDown' ? (chosen + 1) % shown : (chosen <= 0 ? shown - 1 : chosen - 1);
      for (let r = 0; r < ROWS; r++) rows[r].btn.classList.toggle('on', r === chosen);
      engine.setHoverNode(rows[chosen].node);
    });

    rows.forEach((row) => {
      on(row.btn, 'pointerenter', () => { if (row.node >= 0) engine.setHoverNode(row.node); });
      on(row.btn, 'pointerleave', () => engine.setHoverNode(-1));
      on(row.btn, 'focus', () => { if (row.node >= 0) engine.setHoverNode(row.node); });
      on(row.btn, 'blur', () => engine.setHoverNode(-1));
      on(row.btn, 'click', () => { if (row.node >= 0) open(row.node, -1); });
    });

    on(legend, 'click', (e) => {
      const chip = e.target.closest ? e.target.closest('.gx-chip') : null;
      if (!chip) return;
      const ki = +chip.getAttribute('data-kind');
      kindOn[ki] = kindOn[ki] ? 0 : 1;
      chip.setAttribute('aria-pressed', kindOn[ki] ? 'true' : 'false');
      engine.setKind(ki, kindOn[ki]);
      runQuery();
    });

    const ro = new ResizeObserver(measure);
    ro.observe(stage);
    on(window, 'resize', measure);
    if (compact) on(window, 'resize', sizeHero);
    if (moreLink) on(moreLink, 'click', () => { carry = input.value; });
    const hasFile = (e) => !!e.dataTransfer && [...e.dataTransfer.types].includes('Files');
    on(stage, 'dragover', (e) => { if (hasFile(e)) { e.preventDefault(); stage.classList.add('gx-dropping'); } });
    on(stage, 'dragleave', () => stage.classList.remove('gx-dropping'));
    on(stage, 'drop', (e) => {
      if (!hasFile(e)) return;
      e.preventDefault(); stage.classList.remove('gx-dropping');
      upload(e.dataTransfer.files[0]);
    });
    off.signal.addEventListener('abort', () => ro.disconnect());
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
    on(calm, 'change', () => engine.setReducedMotion(calm.matches));
    on(window, 'v2:theme', paint);              // the shell's theme switch
  }

  // Colours for the canvas and for the dots that repeat them, from the active theme.
  function paint() {
    const p = readPalette();
    for (const kd of model.kinds) kd.color = kd.hub ? p.hub : (kd.slot < p.hues.length ? p.hues[kd.slot] : p.other);
    setRamp(p.ramp);
    engine.setColours(p);
    scaleBar.style.background = spectrumCss();
    legend.querySelectorAll('.gx-dot').forEach((dot, ki) => { dot.style.background = model.kinds[ki].color; });
    renderList();
  }

  // A stand-in result: the given nodes at full strength, no statements. null when none of the ids is on the map.
  function pinOf(ids) {
    const out = {
      active: true, ms: 0, nodeHits: 0, itemHits: 0,
      nodeRel: new Float32Array(model.n), nodeOrder: new Uint16Array(model.n), nodeCnt: new Uint16Array(model.n),
      bestItem: new Int32Array(model.n).fill(-1), itemRel: new Float32Array(0), itemOrder: new Int32Array(0),
    };
    for (const id of ids || []) {
      const i = model.indexOf(id);
      if (i >= 0 && out.nodeRel[i] === 0) { out.nodeRel[i] = 1; out.nodeOrder[out.nodeHits++] = i; }
    }
    return out.nodeHits ? out : null;
  }
  function pin(ids, label) {
    if (!engine) return 0;
    const next = pinOf(ids);
    if (!next) { if (pinned) runQuery(); return 0; }
    pinned = next; pinnedIds = [...ids]; pinnedLabel = label || '';
    chosen = -1;
    engine.apply(pinned);
    renderList(); hideTip();
    return next.nodeHits;
  }
  // What is under a point of the viewport: the same hit test the pointer uses.
  function nodeAt(clientX, clientY) {
    if (!engine) return null;
    const r = canvas.getBoundingClientRect();
    const i = engine.hit(clientX - r.left, clientY - r.top);
    if (i < 0) return null;
    const p = engine.nodeScreen(i), best = cur().active ? cur().bestItem[i] : -1;
    return {
      id: model.ids[i], kind: model.kindName[i], title: model.labels[i], text: best >= 0 ? model.itexts[best] : model.subs[i],
      ref: model.sourceRef(i, best), rect: { x: r.left + p.x - p.r - 4, y: r.top + p.y - p.r - 4, w: 2 * p.r + 8, h: 2 * p.r + 8 },
    };
  }
  // The drag-to-chat tool asks the stage what is under the pointer (web/v2/js/aiifier/units.js).
  stage.setAttribute('data-ai-probe', '');
  stage.aiUnitAt = (x, y) => {
    const nd = nodeAt(x, y);
    if (!nd) return null;
    const [, ...rest] = nd.id.split(':');
    return { rect: nd.rect, item: { kind: 'graph-node', title: nd.title, text: nd.text || '', source_refs: nd.ref ? [nd.ref] : [], ids: { node_id: nd.id, clio_id: rest.join(':') } } };
  };

  // Asks the server whether the graph has a newer version; 304 costs no body and no parsing.
  async function check(force) {
    if (data || !model || checking || off.signal.aborted || (!force && Date.now() - fetchedAt < REFRESH_MS)) return false;
    checking = true; fetchedAt = Date.now();
    try {
      const res = await fetch(`/api/matters/${encodeURIComponent(matterId)}/graph`, { headers: { 'If-None-Match': `"${model.version}"` } });
      if (res.status !== 200) return false;
      const payload = await res.json();
      if (off.signal.aborted || !payload || payload.version === model.version) return false;
      return adopt(payload);
    } catch { return false; } finally { checking = false; }
  }

  // A file dropped on the map is sent to our own server (never to Clio), read there, and flies in when the
  // graph's next version holds it. Routes and status fields: server ingest (documents/upload, ingestions/{id}).
  function say(text, bad) { dropNote.hidden = !text; dropNote.textContent = text || ''; dropNote.classList.toggle('gx-error', !!bad); }
  async function upload(file) {
    if (data || !file || !model) return;
    if (!/^(application\/pdf|image\/png|image\/jpeg)$/.test(file.type)) { say('Only PDF, PNG and JPEG files can be added to the file.', true); return; }
    const base = `/api/matters/${encodeURIComponent(matterId)}`;
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    say(`Adding ${file.name}…`);
    try {
      const form = new FormData();
      form.append('file', file);
      const sent = await fetch(`${base}/documents/upload`, { method: 'POST', body: form });
      let job = await sent.json().catch(() => ({}));
      if (!sent.ok) throw new Error(typeof job.detail === 'string' ? job.detail : sent.statusText);
      if (job.duplicate_of) {
        say(`${file.name} is already in the file.`);
        handle.focusNode(`document:${job.duplicate_of.document_id}`);
      } else {
        for (let i = 0; i < 240 && job.state !== 'complete' && job.state !== 'failed'; i++) {
          await wait(1000);
          if (off.signal.aborted) return;
          job = await (await fetch(`${base}/ingestions/${encodeURIComponent(job.id)}`)).json();
          const pages = job.counts && job.counts.pages_total;
          const doing = STAGE_WORDS[job.stage];
          say(`Reading ${file.name}${pages ? ` (${plural(pages, 'page', 'pages')})` : ''}${doing ? `: ${doing}` : ''}…`);
        }
        if (job.state !== 'complete') throw new Error(job.error || 'it did not finish');
        await check(true);
        const found = handle.focusNode(job.node_id);
        say(found ? `${file.name} is on the map.` : `${file.name} was read; the map will show it shortly.`);
      }
      await wait(5000);
      if (!dropNote.classList.contains('gx-error')) say('');
    } catch (err) { say(`${file.name} could not be added: ${err.message}`, true); }
  }

  // Takes a payload on screen. Called once at load, and again when update() finds a newer graph.
  function adopt(payload) {
    const next = buildModel(payload || {});
    if (!next.n) {
      status.hidden = false;
      status.textContent = 'Nothing in this file has been read yet, so there is no map to draw.';
      input.disabled = true;
      return false;
    }
    const first = !engine;
    const before = model ? new Set(model.ids) : null, view = engine ? engine.getView() : null;
    if (engine) engine.destroy();
    model = next;
    search = createSearch(payload, model);
    kindOn = new Uint8Array(model.kinds.length).fill(1);
    if (first && carry) { input.value = carry; carry = ''; }
    engine = createEngine(canvas, model, { compact, labelFont: getComputedStyle(root).fontFamily });
    engine.setReducedMotion(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    legend.replaceChildren(...model.kinds.map((kd, ki) => {
      const dot = el('span', { class: 'gx-dot' });
      return el('button', { type: 'button', class: 'gx-chip', 'data-kind': String(ki), 'aria-pressed': 'true', title: `Hide or show: ${kd.label.toLowerCase()}` },
        dot, el('span', { text: kd.label }), el('span', { class: 'gx-chip-n', text: String(kd.count) }));
    }));
    status.hidden = true; input.disabled = false;
    if (first) wire();
    search.run(input.value, kindOn);
    pinned = pinnedIds ? pinOf(pinnedIds) : null;
    paint();
    measure();
    engine.setView(view);                           // zoom and pan survive a newer version
    engine.apply(cur());
    if (before) {                                   // what this version added arrives from the edge; nothing else moves
      const fresh = [];
      for (let i = 0; i < model.n; i++) if (!before.has(model.ids[i])) fresh.push(i);
      if (fresh.length) engine.enter(fresh);
    }
    return true;
  }

  async function load() {
    fetchedAt = Date.now();
    return data || api(`/api/matters/${encodeURIComponent(matterId)}/graph`);
  }

  const ready = (async () => {
    let payload;
    try { payload = await load(); } catch (err) {
      status.textContent = `The map could not be loaded: ${err.message}`;
      status.classList.add('gx-error');
      return false;
    }
    return off.signal.aborted ? false : adopt(payload);
  })();

  const handle = {
    ready,
    ownsSearch: true,
    // Lights the given nodes and dims the rest; an empty list clears. Returns how many ids are on the map.
    highlightNodes(ids, opts) { return pin(ids, opts && opts.label); },
    highlight(ids) { return pin(ids, ''); },
    // Rings one node and, in the Graph tab, brings it to the middle. False when the id is not on the map.
    focusNode(id) {
      const i = model ? model.indexOf(id) : -1;
      if (i < 0) return false;
      engine.centreOn(i);
      return true;
    },
    nodeAt,
    refresh() { return check(true); },
    setQuery(q) { input.value = q || ''; if (engine) runQuery(); },
    focusSearch() { input.focus(); input.select(); },
    focus() { input.focus(); },
    // The shell calls this on its refresh tick. A newer graph (an uploaded document) replaces the picture;
    // the query, the zoom and anything lit from outside are kept, and the new nodes fly in.
    update() { return check(false); },
    stats(reset) { return engine ? engine.stats(reset) : null; },
    results() {                                      // what the last query found, for tests and timing
      if (!search) return null;
      const r = search.res;
      return { query: r.query, ms: r.ms, items: r.nodeHits, statements: r.itemHits, relevance: Array.from(r.nodeOrder.subarray(0, Math.min(r.nodeHits, 40)), (i) => +r.nodeRel[i].toFixed(3)) };
    },
    // Types `text` one character at a time and runs `frames` frames by hand after each keystroke, timing each:
    // the cost of a typed query on the main thread, measurable even where requestAnimationFrame is throttled.
    bench(text, frames = 9, dt = 1000 / 60) {
      if (!engine) return null;
      const cost = [], query = [];
      for (let k = 1; k <= text.length; k++) {
        input.value = text.slice(0, k);
        const t0 = performance.now();
        runQuery();
        query.push(performance.now() - t0);
        for (let f = 0; f < frames; f++) cost.push(engine.tick(dt).ms);
      }
      let settle = 0;
      while (engine.tick(dt).moving && settle < 600) settle++;
      const pct = (arr, f) => (arr.length ? +[...arr].sort((p, q) => p - q)[Math.min(arr.length - 1, Math.floor(arr.length * f))].toFixed(2) : null);
      return {
        keystrokes: text.length, frames: cost.length, settleFrames: settle,
        frameMs: { median: pct(cost, 0.5), p95: pct(cost, 0.95), max: pct(cost, 1) },
        keystrokeMs: { median: pct(query, 0.5), max: pct(query, 1) },
      };
    },
    destroy() { off.abort(); if (engine) engine.destroy(); root.remove(); if (current === handle) current = null; },
  };
  current = handle;
  return handle;
}
