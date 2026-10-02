// Colour scheme and density for the v2 shell. The colours live in ../css/themes.css; this file only sets three
// attributes on <html> (data-theme = the background, data-accent, data-density), remembers them per browser, and
// tells anything that paints outside CSS (the graph canvas) to redraw. Nobody picks an exact colour: a scheme is a
// curated background combined with a curated accent, and every combination is contrast-checked in themes.css.
//
//   themeColours()            resolved colours of the active pair, cached until it changes
//   'v2:theme' on window      fired after a change; detail = { theme, accent, density, colours }
//   themePicker()             the Settings control: a row of backgrounds, a row of accents, a live preview, density
//   themeSwitch()             the top-bar control: a button that opens the same two swatch rows in a popover;
//                             mounts itself into #v2-theme if the page has one
//
// The first paint is handled by the inline snippet in index.html, which sets the same attributes from the same
// storage keys before any stylesheet is applied.

export const THEMES = [          // backgrounds; the keys are what is stored
  ['light', 'Light', 'White cards on a pale ground'],
  ['firm', 'Mist', 'Light, with a cool tint'],
  ['paper', 'Paper', 'Warm and low contrast'],
  ['dark', 'Dark', 'Neutral dark'],
  ['navy', 'Navy', 'Deep blue dark'],
  ['contrast', 'High contrast', 'Strongest text and borders'],
];
export const ACCENTS = [
  ['blue', 'Blue'], ['sky', 'Sky'], ['teal', 'Teal'], ['violet', 'Violet'], ['orchid', 'Orchid'], ['slate', 'Slate'], ['graphite', 'Graphite'],
];
export const DENSITIES = [['comfortable', 'Comfortable'], ['compact', 'Compact']];
const root = document.documentElement;
// Each page that offers the choice keeps it under its own keys: the firm's shell uses "v2", the provider page sets
// data-theme-ns="pv" in its inline snippet, so one never changes the other even in the same browser.
const NS = root.dataset.themeNs || 'v2';
const KEY_T = `${NS}:theme`, KEY_A = `${NS}:accent`, KEY_D = `${NS}:density`;
const known = (list, v, d) => (list.some(([k]) => k === v) ? v : d);
const nameOf = (list, k) => list.find(([x]) => x === k)[1];
const read = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode: the choice lasts for the page */ } };

export const getTheme = () => known(THEMES, root.dataset.theme || read(KEY_T), 'light');
export const getAccent = () => known(ACCENTS, root.dataset.accent || read(KEY_A), 'blue');
export const getDensity = () => known(DENSITIES, root.dataset.density || read(KEY_D), 'comfortable');
export const pairName = (t = getTheme(), a = getAccent()) => `${nameOf(THEMES, t)} with ${nameOf(ACCENTS, a).toLowerCase()}`;

let cache = null;
const rgb = (hex) => { const n = parseInt(hex.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };

export function themeColours() {
  if (cache) return cache;
  const cs = getComputedStyle(root);
  const v = (name) => cs.getPropertyValue(name).trim();
  const many = (prefix, n) => Array.from({ length: n }, (_, i) => v(`${prefix}${i + 1}`));
  const ramp = many('--g-r', 5);
  cache = Object.freeze({
    theme: getTheme(), accentName: getAccent(), dark: cs.colorScheme === 'dark',
    surface: v('--n-0'), ground: v('--n-50'), wash: v('--g-wash'), line: v('--n-200'),
    ink: v('--n-900'), label: v('--n-600'), muted: v('--n-500'), faint: v('--n-400'),
    accent: v('--accent'), accentStrong: v('--accent-strong'), accentSoft: v('--accent-soft'), onAccent: v('--on-accent'),
    ok: v('--ok'), warn: v('--warn'), bad: v('--bad'), mark: v('--mark'), markInk: v('--mark-ink'),
    hues: many('--g-', 8), other: v('--g-other'), hub: v('--g-hub'),
    ramp: ramp.map(rgb),             // the accent's link ramp, weakest to strongest match, as [r, g, b]
    rampCss: ramp,                   // the same five steps as css colours
  });
  return cache;
}

let switching = 0;
function apply(theme, accent, density) {
  // One frame with transitions off, so every surface changes together instead of some of them cross-fading.
  root.setAttribute('data-theme-switching', '');
  root.dataset.theme = theme; root.dataset.accent = accent; root.dataset.density = density;
  cache = null;
  cancelAnimationFrame(switching);
  switching = requestAnimationFrame(() => { switching = requestAnimationFrame(() => root.removeAttribute('data-theme-switching')); });
  window.dispatchEvent(new CustomEvent('v2:theme', { detail: { theme, accent, density, colours: themeColours() } }));
}
export function setTheme(theme) { theme = known(THEMES, theme, 'light'); if (theme === root.dataset.theme) return; write(KEY_T, theme); apply(theme, getAccent(), getDensity()); }
export function setAccent(accent) { accent = known(ACCENTS, accent, 'blue'); if (accent === root.dataset.accent) return; write(KEY_A, accent); apply(getTheme(), accent, getDensity()); }
export function setDensity(density) { density = known(DENSITIES, density, 'comfortable'); if (density === root.dataset.density) return; write(KEY_D, density); apply(getTheme(), getAccent(), density); }
export function setPair(theme, accent) {
  theme = known(THEMES, theme, 'light'); accent = known(ACCENTS, accent, 'blue');
  if (theme === root.dataset.theme && accent === root.dataset.accent) return;
  write(KEY_T, theme); write(KEY_A, accent); apply(theme, accent, getDensity());
}

// Another tab changed the choice: follow it.
window.addEventListener('storage', (e) => {
  if (e.key === KEY_T || e.key === KEY_A || e.key === KEY_D) apply(known(THEMES, read(KEY_T), 'light'), known(ACCENTS, read(KEY_A), 'blue'), known(DENSITIES, read(KEY_D), 'comfortable'));
});

// ---------------------------------------------------------------- controls

function h(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, val] of Object.entries(props)) { if (val == null || val === false) continue; if (k === 'text') n.textContent = val; else if (k === 'class') n.className = val; else n.setAttribute(k, val === true ? '' : val); }
  n.append(...kids.flat().filter((x) => x != null && x !== false));
  return n;
}
const dot = (c) => h('i', { style: `background:var(${c})` });
// A swatch carries its own data-theme and data-accent, so themes.css paints it: no colour is copied into script.
const bgSwatch = () => h('span', { class: 'th-sw', 'aria-hidden': 'true' },
  h('span', { class: 'th-sw-nav' }, h('i'), h('i'), h('i')),
  h('span', { class: 'th-sw-main' }, h('span', { class: 'th-sw-card' }, h('b')), h('span', { class: 'th-sw-card' }, h('b'))));
const accentSwatch = () => h('span', { class: 'th-ac', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'));

// Every mounted control repaints on a change. The set holds only weak references to each control's root node, and
// the node carries its own repaint, so a Settings picker that has left the page is neither kept alive nor repainted.
// A control is dropped once it has been collected, or is off the page after having been on it, or has been off the
// page for a minute (a control still being mounted is off the page only for a moment, and must not be dropped).
const controls = new Set();
const STALE_MS = 60000;
const gone = (node) => !node || (!node.isConnected && (node.themeSeen || performance.now() - node.themeBorn > STALE_MS));
const sweep = () => { for (const ref of controls) if (gone(ref.deref())) controls.delete(ref); };
const track = (node, paint) => { sweep(); node.themeRepaint = paint; node.themeBorn = performance.now(); controls.add(new WeakRef(node)); paint(); };
export const controlCount = () => controls.size;      // for tests
window.addEventListener('v2:theme', () => {
  sweep();
  for (const ref of controls) { const node = ref.deref(); if (!node) continue; if (node.isConnected) node.themeSeen = true; node.themeRepaint(); }
});

// Arrow keys move within a radio group. With `choose` the newly focused option is also chosen (the Settings picker);
// without it the arrows only move and Enter or Space chooses (the popover).
function roving(group, sel, choose = true) {
  group.addEventListener('keydown', (e) => {
    const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!d) return;
    const items = [...group.querySelectorAll(sel)], i = items.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const next = items[(i + d + items.length) % items.length];
    next.focus(); if (choose) next.click();
  });
}

// The two swatch rows, shared by the Settings picker and the provider page's popover. paint() refreshes the checked
// state and re-scopes every swatch to the current pair.
export function swatchRows({ chooseOnArrow = true } = {}) {
  const bgRow = h('div', { class: 'th-grid', role: 'radiogroup', 'aria-label': 'Background' },
    THEMES.map(([key, name, hint]) => h('button', { class: 'th-opt', type: 'button', role: 'radio', 'data-key': key, title: hint }, bgSwatch(), h('span', { class: 'th-name', text: name }))));
  const acRow = h('div', { class: 'th-accents', role: 'radiogroup', 'aria-label': 'Accent' },
    ACCENTS.map(([key, name]) => h('button', { class: 'th-opt th-opt-ac', type: 'button', role: 'radio', 'data-key': key }, accentSwatch(), h('span', { class: 'th-name', text: name }))));
  const paint = () => {
    const t = getTheme(), a = getAccent();
    for (const b of bgRow.children) { const on = b.dataset.key === t; b.setAttribute('aria-checked', on); b.tabIndex = on ? 0 : -1; const s = b.firstChild; s.dataset.theme = b.dataset.key; s.dataset.accent = a; }
    for (const b of acRow.children) { const on = b.dataset.key === a; b.setAttribute('aria-checked', on); b.tabIndex = on ? 0 : -1; const s = b.firstChild; s.dataset.theme = t; s.dataset.accent = b.dataset.key; }
  };
  bgRow.addEventListener('click', (e) => { const b = e.target.closest('.th-opt'); if (b) setTheme(b.dataset.key); });
  acRow.addEventListener('click', (e) => { const b = e.target.closest('.th-opt'); if (b) setAccent(b.dataset.key); });
  roving(bgRow, '.th-opt', chooseOnArrow); roving(acRow, '.th-opt', chooseOnArrow);
  track(bgRow, paint);
  return { bgRow, acRow, paint };
}

export function themePicker() {
  const { bgRow, acRow } = swatchRows();
  const seg = h('span', { class: 'seg', role: 'group', 'aria-label': 'Density' },
    DENSITIES.map(([key, name]) => h('button', { type: 'button', 'data-key': key, text: name })));
  // The preview is ordinary shell markup, so it shows exactly what the pair does to a link, a button and the three statuses.
  const pair = h('strong', { class: 'th-pair', 'aria-live': 'polite' });
  const preview = h('div', { class: 'th-preview', 'aria-label': 'Preview' },
    h('div', { class: 'th-preview-h' }, pair, h('span', { class: 'th-cur', text: 'Current page' })),
    h('p', {}, 'Coverage is ', h('span', { class: 'linkish', text: 'linked to its source' }), ' and every status keeps its own colour.'),
    h('div', { class: 'th-preview-row' },
      h('span', { class: 'pill ok', text: '✓ confirmed' }), h('span', { class: 'pill warn', text: '~ assumed' }), h('span', { class: 'pill bad', text: '⚑ contested' }),
      h('span', { class: 'th-ramp', title: 'Graph links, weaker to stronger match' }, dot('--g-r1'), dot('--g-r2'), dot('--g-r3'), dot('--g-r4'), dot('--g-r5')),
      h('span', { class: 'btn primary sm th-btn', text: 'Primary' })));
  const paint = () => {
    const d = getDensity();
    for (const b of seg.children) b.setAttribute('aria-pressed', b.dataset.key === d);
    pair.textContent = pairName();
  };
  seg.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setDensity(b.dataset.key); });
  track(seg, paint);
  return h('div', { class: 'th-picker' },
    h('div', { class: 'th-label', text: 'Background' }), bgRow,
    h('div', { class: 'th-label', text: 'Accent' }), acRow,
    preview,
    h('div', { class: 'th-row' }, h('span', { text: 'Density' }), seg));
}

// The top-bar control. The button only opens the options; nothing changes until a swatch is chosen, and the popover
// stays open so a background and an accent can both be set.
let popSeq = 0;
export function themeSwitch() {
  const id = `th-pop-${++popSeq}`;
  const label = h('span');
  const btn = h('button', { type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', 'aria-controls': id, title: 'Colours' }, h('span', { class: 'th-dot', 'aria-hidden': 'true' }), label);
  const { bgRow, acRow } = swatchRows({ chooseOnArrow: false });
  const pair = h('strong', { class: 'th-pop-pair', 'aria-live': 'polite' });
  const pop = h('div', { class: 'th-pop', id, role: 'dialog', 'aria-label': 'Colours', hidden: true },
    pair, h('div', { class: 'th-label', text: 'Background' }), bgRow, h('div', { class: 'th-label', text: 'Accent' }), acRow);
  const box = h('span', { class: 'th-quick' }, btn, pop);
  const paint = () => { label.textContent = pairName(); pair.textContent = pairName(); btn.setAttribute('aria-label', `Colours: ${pairName()}`); };
  const open = (on, refocus = false) => {
    pop.hidden = !on; btn.setAttribute('aria-expanded', String(on));
    if (on) (pop.querySelector('.th-grid [aria-checked="true"]') || pop.querySelector('button')).focus();
    else if (refocus) btn.focus({ preventScroll: true });
  };
  btn.addEventListener('click', () => open(pop.hidden, true));
  box.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { e.stopPropagation(); open(false, true); } });
  // A click outside closes it and hands focus back to the button, unless what was clicked took focus itself.
  document.addEventListener('pointerdown', (e) => {
    if (pop.hidden || box.contains(e.target)) return;
    open(false);
    setTimeout(() => { if (document.activeElement === document.body || !document.activeElement) btn.focus({ preventScroll: true }); }, 0);
  });
  box.addEventListener('focusout', (e) => { if (!pop.hidden && e.relatedTarget && !box.contains(e.relatedTarget)) open(false); });
  // Choosing re-renders nothing here, so focus stays on the chosen swatch and the popover stays open.
  track(btn, paint);
  return box;
}

// ---------------------------------------------------------------- figures ease instead of jumping
// When a card is redrawn with a different number in the same place, the figure counts from the old value to the new
// one over one --m-med. Only the text of that one element changes; tabular figures keep its width steady.

const FIG = '.v2-fig-v, .mc-num, .v2-stat-v, .wk-count strong';
const NUM = /^(\D*?)(-?\d[\d,]*(?:\.\d+)?)(\D*)$/;
const still = matchMedia('(prefers-reduced-motion: reduce)');
const last = new Map();              // "card key / figure index" -> the text last shown there

function ease(node, from, to) {
  const a = NUM.exec(from), b = NUM.exec(to);
  if (!a || !b || a[1] !== b[1] || a[3] !== b[3]) return;
  const x0 = Number(a[2].replace(/,/g, '')), x1 = Number(b[2].replace(/,/g, ''));
  if (!Number.isFinite(x0) || !Number.isFinite(x1) || x0 === x1) return;
  const dp = (b[2].split('.')[1] || '').length, grouped = b[2].includes(',');
  const fmt = (x) => `${b[1]}${grouped ? x.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp }) : x.toFixed(dp)}${b[3]}`;
  const t0 = performance.now(), ms = 200;
  const step = (now) => {
    if (!node.isConnected) return;
    const p = Math.max(0, Math.min(1, (now - t0) / ms)), k = 1 - (1 - p) ** 3;   // a frame's timestamp can precede t0
    node.textContent = p < 1 ? fmt(x0 + (x1 - x0) * k) : to;
    if (p < 1) requestAnimationFrame(step);
  };
  node.textContent = fmt(x0);
  requestAnimationFrame(step);
}

function watchFigures(view) {
  const scan = () => {
    const seen = new Set();
    view.querySelectorAll('.v2-card').forEach((card, ci) => {
      const key = card.dataset.id || card.dataset.card || card.id || card.querySelector('h3')?.textContent || `#${ci}`;
      card.querySelectorAll(FIG).forEach((node, i) => {
        if (node.childElementCount) return;
        const id = `${key}/${i}`, text = node.textContent, was = last.get(id);
        seen.add(id); last.set(id, text);
        if (was != null && was !== text && !node.dataset.easing && !still.matches) {
          node.dataset.easing = '1'; ease(node, was, text); setTimeout(() => delete node.dataset.easing, 260);
          last.set(id, text);
        }
      });
    });
    for (const id of last.keys()) if (!seen.has(id)) last.delete(id);
  };
  // A card that is new to its grid arrives softly (motion.css .m-arrive); a reorder or a redraw of the same cards does not.
  // Marked here, in the observer's microtask, so the class is on before the card is ever painted.
  const grids = new WeakMap();        // grid element -> ids it held after its last change
  const arrive = (grid) => {
    const before = grids.get(grid), now = new Set();
    let i = 0;
    for (const card of grid.children) {
      const id = card.dataset.id || '+';
      now.add(id);
      if (before?.has(id)) continue;
      card.style.setProperty('--m-i', before ? 0 : Math.min(i++, 9));   // first paint staggers; a later addition does not wait
      card.classList.add('m-arrive');
    }
    grids.set(grid, now);
  };
  // childList only: the tween writes text into an element that is already known, and its own writes are ignored while it runs
  new MutationObserver((muts) => {
    const seenGrids = new Set();
    for (const m of muts) {
      if (m.target.classList?.contains('v2-grid')) seenGrids.add(m.target);
      else for (const n of m.addedNodes) if (n.nodeType === 1) { if (n.classList.contains('v2-grid')) seenGrids.add(n); else n.querySelectorAll?.('.v2-grid').forEach((g) => seenGrids.add(g)); }
    }
    seenGrids.forEach(arrive);
    // Scanned here rather than on the next frame, so a changed figure is never painted once at its new value first.
    if (!muts.every((m) => m.target.dataset?.easing)) scan();
  }).observe(view, { childList: true, subtree: true });
}

// ---------------------------------------------------------------- self-mount in the shell

function boot() {
  // ?theme=navy&accent=teal&density=compact shows a link in that scheme without changing the stored choice.
  const q = new URLSearchParams(location.search);
  if (q.get('theme')) root.dataset.theme = known(THEMES, q.get('theme'), 'light');
  if (q.get('accent')) root.dataset.accent = known(ACCENTS, q.get('accent'), 'blue');
  if (q.get('density')) root.dataset.density = known(DENSITIES, q.get('density'), 'comfortable');
  const t = getTheme(), a = getAccent(), d = getDensity();
  if (root.dataset.theme !== t) root.dataset.theme = t;
  if (root.dataset.accent !== a) root.dataset.accent = a;
  if (root.dataset.density !== d) root.dataset.density = d;
  const slot = document.getElementById('v2-theme');
  if (slot && !slot.firstChild) slot.append(themeSwitch());
  const view = document.getElementById('v2-view');
  if (view) watchFigures(view);
}
boot();
