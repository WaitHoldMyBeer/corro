// The AI-ifier: a round handle in the bottom-left corner. Drag it onto anything on the firm's screen and that
// unit goes into the assistant's context; shift-drag (or shift-click) to pick several; Enter on the handle for
// the keyboard path. What is sent is built in ./units.js: text, source references and ids, never markup.
//
//   mountAiifier()            once, from the shell
//   takeQueued()              items picked while no assistant panel was there
//   connectAssistant(api)     { openPanel, addContext } if the panel is not at ../assistant/index.js
//
// One overlay element is moved with a transform on requestAnimationFrame. Each frame reads first (one hit test,
// one rectangle) and writes after; nothing on the page is restyled while the pointer moves.
import { toast } from '../../../js/util.js';
import { unitAt, unitsIn, describe, labelOf } from './units.js';

const CSS_HREF = new URL('../../css/aiifier.css', import.meta.url).href;
const SVG = 'http://www.w3.org/2000/svg';
const DRAG_START = 5;                 // px of travel before a press becomes a drag
const LIFT = 1.12;                    // the handle grows a little in the hand
const PAD = 3;                        // px the highlight stands off the unit
const MIN_BOX = 44;                   // px: the smallest highlight, so it shows around the 36px handle
const GLIDE_MS = 55;                  // time constant of the highlight's glide between units
const GAP_MS = 220;                   // crossing a gap this quickly keeps the glide instead of a fresh fade-in
const DROP_MS = 440;                  // clears the flash on a drop; longer than its animation in aiifier.css
const EDGE = 56, EDGE_SPEED = 16;     // auto-scroll band (px) and its top speed (px per frame)
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const html = document.documentElement;

export const stats = { frames: 0, maxMs: 0, sumMs: 0, over16: 0 };

const s = {
  mounted: false, mode: 'idle', multi: false, press: null,
  ptr: { x: 0, y: 0, dirty: false, live: false },
  cur: null, target: null, box: null, shown: false, inside: false, lostAt: 0, rectDirty: false,
  sel: new Map(), list: [], cursor: -1, raf: 0, last: 0, vh: innerHeight, armed: false, queue: [], host: null, hostWatch: null,
};
const ui = {};
let assistant = null;

const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const inUi = (t) => t instanceof Element && !!t.closest('.aiifier');
const same = (a, b) => a === b || (!!a && !!b && a.key === b.key);
const plural = (n) => `${n} item${n === 1 ? '' : 's'}`;

// ---------------------------------------------------------------- hand-off

async function getAssistant() {
  if (assistant) return assistant;
  try { const m = await import('../assistant/index.js'); if (typeof m.addContext === 'function') assistant = m; } catch { /* no panel on this page */ }
  return assistant;
}
export function connectAssistant(api) { assistant = api; if (s.queue.length) send([]); }
export const takeQueued = () => s.queue.splice(0);

async function send(items) {
  s.queue.push(...items);
  if (!s.queue.length) return;
  const m = await getAssistant();
  const batch = s.queue.slice();
  window.dispatchEvent(new CustomEvent('aiifier:context', { detail: { items: batch } }));
  if (m) {
    try {
      m.openPanel?.(); m.addContext(batch); s.queue.length = 0;
      announce(`${plural(batch.length)} added to the assistant.`);
      // Over an open dialog the panel is behind it: say so beside the handle.
      if (ui.root.parentElement !== document.body) say(`${batch.length === 1 ? 'Added' : `${batch.length} added`} to the chat. Close this panel to see it.`);
      return;
    } catch (err) { console.error('assistant refused the context', err); }
  }
  toast(`${plural(batch.length)} picked for the assistant. The panel is not open on this page, so ${batch.length === 1 ? 'it is' : 'they are'} held until it is.`);
}

// ---------------------------------------------------------------- geometry (reads)

const clips = new WeakMap();
function clipOf(el) {
  if (clips.has(el)) return clips.get(el);
  let found = null;
  for (let n = el.parentElement; n && n !== document.body && n !== html; n = n.parentElement) {
    if (n.localName === 'dialog') break;
    const cs = getComputedStyle(n);
    if (cs.overflowY !== 'visible' || cs.overflowX !== 'visible') { found = n; break; }
  }
  clips.set(el, found);
  return found;
}

// A target smaller than the handle would be hidden under it: its highlight is grown about its centre.
function roomy(r) {
  const dx = Math.max(0, MIN_BOX - r.w) / 2, dy = Math.max(0, MIN_BOX - r.h) / 2;
  return dx || dy ? { x: r.x - dx, y: r.y - dy, w: r.w + dx * 2, h: r.h + dy * 2 } : r;
}

function rectOf(u) {
  if (u.virtual) {
    const r = u.virtual.rect;
    if (!u.off) return roomy({ x: r.x, y: r.y, w: r.w, h: r.h });
    const p = u.el.getBoundingClientRect();
    return roomy({ x: p.left + u.off.x, y: p.top + u.off.y, w: r.w, h: r.h });
  }
  const r = u.el.getBoundingClientRect();
  let x = r.left, y = r.top, x2 = r.right, y2 = r.bottom;
  const clip = clipOf(u.el);
  if (clip) { const c = clip.getBoundingClientRect(); x = Math.max(x, c.left); y = Math.max(y, c.top); x2 = Math.min(x2, c.right); y2 = Math.min(y2, c.bottom); }
  return x2 - x < 4 || y2 - y < 4 ? null : roomy({ x, y, w: x2 - x, h: y2 - y });
}

function scrollerOf(el) {
  for (let n = el; n && n !== document.body && n !== html; n = n.parentElement) {
    const oy = getComputedStyle(n).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 1) return n;
  }
  return document.scrollingElement || html;
}

// ---------------------------------------------------------------- the frame

function kick() { if (!s.raf) s.raf = requestAnimationFrame(frame); }

function setCurrent(u) {
  if (u && u.virtual) s.rectDirty = true;            // a canvas node: same node, but its box is given afresh each time
  if (same(u, s.cur)) { if (u) s.cur = u; return; }
  s.cur = u; s.rectDirty = true;
  if (u) { const [kind, title] = labelOf(u); ui.tagKind.textContent = kind.replace(/[-_]/g, ' '); ui.tagTitle.textContent = title; }
}

function place(node, r) {
  node.style.transform = `translate3d(${r.x - PAD}px, ${r.y - PAD}px, 0)`;
  node.style.width = `${r.w + PAD * 2}px`;
  node.style.height = `${r.h + PAD * 2}px`;
}

// Glides the one highlight toward its target. Returns true while it still has distance to cover.
function drawHighlight(now, dt) {
  const t = s.target;
  if (!t) {
    if (s.shown) { ui.hl.classList.remove('on'); s.shown = false; s.lostAt = now; }
    return false;
  }
  let b = s.box, moving = false;
  if (!b || reduced.matches || (!s.shown && now - s.lostAt > GAP_MS)) b = s.box = { ...t };
  else {
    const k = 1 - Math.exp(-dt / GLIDE_MS);
    b.x += (t.x - b.x) * k; b.y += (t.y - b.y) * k; b.w += (t.w - b.w) * k; b.h += (t.h - b.h) * k;
    if (Math.abs(t.x - b.x) + Math.abs(t.y - b.y) + Math.abs(t.w - b.w) + Math.abs(t.h - b.h) < 0.6) s.box = b = { ...t };
    else moving = true;
  }
  place(ui.hl, b);
  if (!s.shown) { ui.hl.classList.add('on'); s.shown = true; }
  const inside = b.y < 40;                           // no room above: the label goes inside the box
  if (inside !== s.inside) { s.inside = inside; ui.hl.classList.toggle('in', inside); }
  return moving;
}

function autoScroll() {
  const y = s.ptr.y, inBand = y < EDGE || y > s.vh - EDGE;
  if (!s.armed) { if (!inBand) s.armed = true; return false; }   // the handle starts in the bottom band: wait until it has left
  if (!inBand) return false;
  const v = y < EDGE ? -EDGE_SPEED * (1 - Math.max(0, y) / EDGE) : EDGE_SPEED * (1 - Math.max(0, s.vh - y) / EDGE);
  const sc = s.cur && !s.cur.virtual ? scrollerOf(s.cur.el) : (document.scrollingElement || html);
  sc.scrollBy(0, v);
  s.ptr.dirty = true; s.rectDirty = true;
  return true;
}

function frame(now) {
  s.raf = 0;
  const t0 = performance.now();
  const dt = Math.min(64, s.last ? now - s.last : 16);
  s.last = now;

  // read
  if (s.ptr.dirty) {
    s.ptr.dirty = false;
    if (s.mode === 'drag' || (s.mode === 'pick' && s.ptr.live)) setCurrent(unitAt(s.ptr.x, s.ptr.y));
  }
  if (s.cur && !s.cur.el.isConnected) setCurrent(null);
  if (s.rectDirty) {
    s.rectDirty = false;
    s.target = s.cur ? rectOf(s.cur) : null;
    for (const e of s.sel.values()) e.rect = e.unit.el.isConnected ? rectOf(e.unit) : null;
    for (const e of s.sel.values()) { e.box.hidden = !e.rect; if (e.rect) place(e.box, e.rect); }
  }

  // write
  let more = false;
  if (s.mode === 'drag') ui.btn.style.transform = `translate3d(${s.ptr.x - s.press.x}px, ${s.ptr.y - s.press.y}px, 0) scale(${LIFT})`;
  if (s.mode !== 'idle') more = drawHighlight(now, dt);
  if (s.mode === 'drag') more = autoScroll() || more;

  const ms = performance.now() - t0;
  stats.frames += 1; stats.sumMs += ms; if (ms > stats.maxMs) stats.maxMs = ms; if (ms > 16) stats.over16 += 1;
  if (more) kick(); else s.last = 0;
}

// The handle's home: inside the navigation column, directly above Settings, in the window's bottom-left corner.
// Measured, so a theme's density or column width carries. When the column has no free band there (a short window,
// where the list runs down to Settings) it sits at the right end of the Settings row, where there is never a label.
const SIZE = 32, GAP = 6;
function seat() {
  const nav = document.querySelector('.v2-nav');
  const r = nav && getComputedStyle(nav).position === 'sticky' ? nav.getBoundingClientRect() : null;
  let left = 16, bottom = 14;
  if (r && r.width > 120) {
    left = Math.round(r.right - 48);
    const foot = nav.querySelector('.v2-nav-foot')?.getBoundingClientRect();
    const last = nav.querySelector('#v2-navlist')?.getBoundingClientRect();
    if (foot && last && foot.bottom <= innerHeight + 1 && foot.top - last.bottom >= SIZE + GAP * 2) {
      left = Math.round(r.left + 15);                 // centred on the column of navigation glyphs
      bottom = Math.round(innerHeight - foot.top + GAP);
    }
  }
  ui.root.style.setProperty('--ai-left', `${left}px`);
  ui.root.style.setProperty('--ai-bottom', `${bottom}px`);
}

const onScroll = () => { s.rectDirty = true; s.ptr.dirty = true; kick(); };
const onResize = () => { s.vh = innerHeight; onScroll(); };

let dropTimer = 0;
function clearHighlight(flash) {
  s.cur = null; s.target = null; s.box = null;
  if (flash && s.shown && !reduced.matches) {
    ui.hl.classList.add('drop');
    clearTimeout(dropTimer);
    dropTimer = setTimeout(() => ui.hl.classList.remove('drop', 'on'), DROP_MS);   // a timer, so a throttled tab cannot leave it lit
  } else ui.hl.classList.remove('on', 'drop');
  s.shown = false; s.lostAt = 0;
}

// ---------------------------------------------------------------- dragging the handle

function onDown(e) {
  if (e.button !== 0 || s.press) return;
  e.preventDefault();                                // no text selection, no native drag
  if (s.mode === 'pick') { exitPick(); return; }
  s.press = { x: e.clientX, y: e.clientY, id: e.pointerId, shift: e.shiftKey, moved: false };
  s.ptr.x = e.clientX; s.ptr.y = e.clientY;
  try { ui.btn.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer has nothing to capture */ }
  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onCancel);
  window.addEventListener('keydown', onDragKey, true);
}

function onMove(e) {
  const p = s.press;
  if (!p || e.pointerId !== p.id) return;
  s.ptr.x = e.clientX; s.ptr.y = e.clientY; s.ptr.dirty = true;
  if (!p.moved) {
    if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < DRAG_START) return;
    p.moved = true; s.mode = 'drag'; s.armed = false; s.vh = innerHeight;
    ui.btn.classList.remove('back'); ui.hint.classList.remove('show');
    clearTimeout(dropTimer); ui.hl.classList.remove('drop');
    ui.root.classList.add('dragging'); html.classList.add('aiifier-dragging');
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
  }
  kick();
}

let backTimer = 0;
function release() {
  const p = s.press;
  s.press = null;
  window.removeEventListener('pointermove', onMove);
  window.removeEventListener('pointerup', onUp);
  window.removeEventListener('pointercancel', onCancel);
  window.removeEventListener('keydown', onDragKey, true);
  window.removeEventListener('scroll', onScroll, true);
  try { ui.btn.releasePointerCapture(p.id); } catch { /* never captured */ }
  if (!p.moved) return p;
  s.mode = 'idle';
  ui.root.classList.remove('dragging'); html.classList.remove('aiifier-dragging');
  ui.btn.classList.add('back');                      // springs home
  ui.btn.style.transform = '';
  clearTimeout(backTimer);
  backTimer = setTimeout(() => ui.btn.classList.remove('back'), 400);   // transitionend does not fire in a throttled tab
  return p;
}

function onUp(e) {
  if (!s.press || e.pointerId !== s.press.id) return;
  const shift = e.shiftKey || s.press.shift;
  // A release far from the press is a drop even if no move was delivered in between (a very fast flick).
  if (!s.press.moved && Math.hypot(e.clientX - s.press.x, e.clientY - s.press.y) >= DRAG_START) s.press.moved = true;
  const u = s.press.moved ? unitAt(e.clientX, e.clientY) : null;
  const p = release();
  if (!p.moved) { if (shift) enterPick({ multi: true }); else tap(); return; }
  if (shift) { clearHighlight(false); enterPick({ multi: true, first: u }); return; }
  if (!u) { clearHighlight(false); return; }
  const item = describe(u);
  clearHighlight(true);
  send([item]);
}

function onCancel(e) { if (s.press && e.pointerId === s.press.id) { release(); clearHighlight(false); } }
function onDragKey(e) { if (e.key === 'Escape' && s.press) { e.preventDefault(); e.stopPropagation(); release(); clearHighlight(false); } }

const HINT = 'Drag onto anything to bring it into the assistant. Shift to pick several.';
let hintTimer = 0;
function say(text, ms = 3200) {
  ui.hint.textContent = text;
  ui.hint.classList.add('show');
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => { ui.hint.classList.remove('show'); ui.hint.textContent = HINT; }, ms);
}
// A plain click is not a second Assistant button: it only says what the handle is for.
function tap() { say(HINT); }

// ---------------------------------------------------------------- pick mode: several units, or the keyboard path

const BLOCKED = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'dblclick', 'auxclick', 'contextmenu'];
const block = (e) => { if (!inUi(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); } };

function scope() {
  return ui.root.parentElement?.localName === 'dialog' ? ui.root.parentElement : (document.getElementById('v2-view') || document.body);
}

function enterPick({ multi, first = null, keys = false }) {
  s.mode = 'pick'; s.multi = multi; s.ptr.live = false; s.list = []; s.cursor = -1; s.vh = innerHeight;
  ui.root.classList.add('picking'); html.classList.add('aiifier-picking');
  for (const type of BLOCKED) window.addEventListener(type, block, true);
  window.addEventListener('click', onPickClick, true);
  window.addEventListener('pointermove', onPickMove, { capture: true, passive: true });
  window.addEventListener('keydown', onPickKey, true);
  window.addEventListener('scroll', onScroll, { capture: true, passive: true });
  window.addEventListener('resize', onResize);
  window.addEventListener('hashchange', exitPick);
  if (first) toggle(first);
  paintBar();
  if (keys) step(1);
  else announce(multi ? 'Pick mode. Click each thing to bring into the chat, then Add. Escape cancels.' : 'Pick mode.');
  kick();
}

function exitPick(flash) {
  if (s.mode !== 'pick') return;
  s.mode = 'idle';
  ui.root.classList.remove('picking'); html.classList.remove('aiifier-picking');
  for (const type of BLOCKED) window.removeEventListener(type, block, true);
  window.removeEventListener('click', onPickClick, true);
  window.removeEventListener('pointermove', onPickMove, true);
  window.removeEventListener('keydown', onPickKey, true);
  window.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('resize', onResize);
  window.removeEventListener('hashchange', exitPick);
  for (const e of s.sel.values()) e.box.remove();
  s.sel.clear(); s.list = []; s.cursor = -1;
  clearHighlight(flash === true);
}

function finish(entries) {
  const items = entries.map((e) => e.item);
  if (!reduced.matches) {
    for (const e of entries) {
      if (!e.box) continue;
      const ghost = e.box.cloneNode(false);            // the picked boxes fade out after the mode has gone
      ghost.classList.add('drop'); ui.layer.append(ghost);
      setTimeout(() => ghost.remove(), DROP_MS);
    }
  }
  const focusBack = document.activeElement === ui.btn;
  exitPick(!entries.some((e) => e.box));
  if (focusBack) ui.btn.focus({ preventScroll: true });
  send(items);
}

function toggle(u) {
  const had = s.sel.get(u.key);
  if (had) { had.box.remove(); s.sel.delete(u.key); }
  else {
    if (u.virtual) { const p = u.el.getBoundingClientRect(); u.off = { x: u.virtual.rect.x - p.left, y: u.virtual.rect.y - p.top }; }
    const box = h('div', 'aiifier-sel');
    ui.layer.append(box);
    s.sel.set(u.key, { unit: u, item: describe(u), box, rect: null });
  }
  s.rectDirty = true;
  paintBar();
  announce(`${had ? 'Removed' : 'Selected'}. ${plural(s.sel.size)} selected.`);
  kick();
}

function paintBar() {
  const n = s.sel.size;
  if (s.multi) {
    ui.barText.textContent = n ? `${plural(n)} selected` : 'Click each thing to bring into the chat';
    ui.add.textContent = n ? `Add ${n} to chat` : 'Add to chat';
    ui.add.disabled = !n && s.cursor < 0;
  } else {
    ui.barText.textContent = 'Arrow keys to move, Enter to add';
    ui.add.textContent = 'Add to chat';
    ui.add.disabled = s.cursor < 0;
  }
}

function onPickMove(e) { s.ptr.x = e.clientX; s.ptr.y = e.clientY; s.ptr.dirty = true; s.ptr.live = true; kick(); }

function onPickClick(e) {
  if (inUi(e.target)) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const u = unitAt(e.clientX, e.clientY);
  if (!u) return;
  if (s.multi) toggle(u); else finish([{ item: describe(u) }]);
}

function step(d, to = null) {
  if (!s.list.length || !s.list[Math.max(0, s.cursor)]?.el.isConnected) { s.list = unitsIn(scope()); s.cursor = -1; }
  const n = s.list.length;
  if (!n) { announce('There is nothing on this page to pick.'); return; }
  s.cursor = to != null ? to : Math.max(0, Math.min(n - 1, s.cursor + d));
  const u = s.list[s.cursor];
  s.ptr.live = false;                                // the keyboard leads until the pointer moves again
  u.el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  setCurrent(u); s.rectDirty = true;
  const [kind, title] = labelOf(u);
  announce(`${kind}: ${title}. ${s.cursor + 1} of ${n}.${s.sel.has(u.key) ? ' Selected.' : ''}`);
  paintBar(); kick();
}

function addNow() {
  const cur = s.cursor >= 0 ? s.list[s.cursor] : null;
  if (s.multi && s.sel.size) finish([...s.sel.values()]);
  else if (cur) finish([{ item: describe(cur) }]);
}

function onPickKey(e) {
  const k = e.key;
  if (k === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); exitPick(); announce('Cancelled.'); return; }
  if (inUi(e.target) && e.target !== ui.btn) return;                 // the bar's own buttons work as buttons
  const last = () => { if (!s.list.length) s.list = unitsIn(scope()); return s.list.length - 1; };
  if (k === 'ArrowDown' || k === 'ArrowRight') step(1);
  else if (k === 'ArrowUp' || k === 'ArrowLeft') step(-1);
  else if (k === 'Home') step(0, 0);
  else if (k === 'End') step(0, last());
  else if (k === ' ') { const cur = s.list[s.cursor]; if (cur) { if (s.multi) toggle(cur); else addNow(); } }
  else if (k === 'Enter') addNow();
  else return;
  e.preventDefault(); e.stopImmediatePropagation();
}

function announce(text) { ui.live.textContent = text; }

// ---------------------------------------------------------------- staying usable over an open panel
// A modal dialog makes the rest of the page inert, the handle included. While one is open the tool lives
// inside it (never the sign-in dialog), and goes back to the page when it closes.

function rehome() {
  const open = [...document.querySelectorAll('dialog[open]')].filter((d) => d.matches(':modal') && !d.matches('.signin-dlg, [data-ai-skip]'));
  const host = open[open.length - 1] || document.body;
  if (ui.root.parentElement === host) return;
  if (s.mode === 'pick') exitPick();
  s.hostWatch?.disconnect();
  if (host !== document.body) {
    ui.root.classList.add('settling');                // the dialog's own entrance moves fixed children with it: wait it out
    setTimeout(() => ui.root.classList.remove('settling'), 280);
    s.hostWatch = new MutationObserver(() => { if (ui.root.parentElement !== host && host.open) host.append(ui.root); });
    s.hostWatch.observe(host, { childList: true });
  }
  host.append(ui.root);
}

// ---------------------------------------------------------------- mount

function glyph() {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true');
  for (const d of ['M10 3.5l1.9 5.1 5.1 1.9-5.1 1.9L10 17.5l-1.9-5.1L3 10.5l5.1-1.9z', 'M18 14l.9 2.4 2.4.9-2.4.9L18 20.6l-.9-2.4-2.4-.9 2.4-.9z']) {
    const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); svg.append(p);
  }
  return svg;
}

export function mountAiifier() {
  if (s.mounted) return;
  s.mounted = true;
  if (![...document.styleSheets].some((x) => x.href === CSS_HREF) && !document.querySelector(`link[href="${CSS_HREF}"]`)) {
    const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = CSS_HREF; document.head.append(l);
  }
  ui.root = h('div', 'aiifier'); ui.root.setAttribute('data-ai-skip', '');
  ui.layer = h('div', 'aiifier-layer'); ui.layer.setAttribute('aria-hidden', 'true');
  ui.hl = h('div', 'aiifier-hl');
  ui.tagKind = h('b'); ui.tagTitle = h('span');
  const tag = h('div', 'aiifier-tag'); tag.append(ui.tagKind, ui.tagTitle);
  ui.hl.append(tag); ui.layer.append(ui.hl);

  ui.btn = h('button', 'aiifier-btn'); ui.btn.type = 'button';
  ui.btn.setAttribute('aria-label', 'Bring something on this page into the assistant. Drag onto it, or press Enter to choose with the arrow keys.');
  ui.btn.setAttribute('aria-describedby', 'aiifier-hint');
  ui.btn.append(glyph());
  ui.hint = h('div', 'aiifier-hint', HINT);
  ui.hint.id = 'aiifier-hint'; ui.hint.setAttribute('role', 'tooltip');

  ui.barText = h('span', 'aiifier-bar-t');
  ui.add = h('button', 'btn primary small', 'Add to chat'); ui.add.type = 'button';
  const cancel = h('button', 'btn quiet small', 'Cancel'); cancel.type = 'button'; cancel.append(h('kbd', '', 'Esc'));
  ui.bar = h('div', 'aiifier-bar'); ui.bar.setAttribute('role', 'toolbar'); ui.bar.setAttribute('aria-label', 'Bring into the chat');
  ui.bar.append(ui.barText, ui.add, cancel);
  ui.live = h('div', 'aiifier-live'); ui.live.setAttribute('aria-live', 'polite');
  ui.root.append(ui.layer, ui.btn, ui.hint, ui.bar, ui.live);
  document.body.append(ui.root);

  ui.btn.addEventListener('pointerdown', onDown);
  ui.btn.addEventListener('dragstart', (e) => e.preventDefault());
  ui.btn.addEventListener('transitionend', (e) => { if (e.propertyName === 'transform') ui.btn.classList.remove('back'); });
  ui.btn.addEventListener('click', (e) => {            // pointer presses are handled above; this is Enter or Space
    if (e.detail !== 0) return;
    if (s.mode === 'pick') exitPick(); else enterPick({ multi: e.shiftKey, keys: true });
  });
  ui.add.addEventListener('click', addNow);
  cancel.addEventListener('click', exitPick);

  new MutationObserver(rehome).observe(document.body, { attributes: true, attributeFilter: ['open'], subtree: true });
  rehome();
  seat();
  window.addEventListener('resize', seat);
  document.fonts?.ready.then(seat);
  const list = document.getElementById('v2-navlist');   // painted by the shell; its height changes with a theme's density
  if (list && window.ResizeObserver) new ResizeObserver(seat).observe(list);
}
