// Shared pieces for the money cards: scoped styles, status marks, figure tiles, component bands.
// Every amount printed is the server's; the only numbers made here are bar widths (geometry).
import { svgEl } from '../../../js/util.js';

const CSS = `
.mc { --mc-ink: var(--ink, #14181f); --mc-mute: var(--muted, #566070); --mc-line: var(--line, #dde1e7);
  --mc-ok: var(--ok, #17775a); --mc-warn: var(--warn, #a15c00); --mc-bad: var(--bad, #b3261e); --mc-unk: var(--unknown, #8a93a0);
  --mc-acc: var(--accent, #1f4fd8); color: var(--mc-ink); display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.mc .mc-figs { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px 16px; }
.mc .mc-fig { display: flex; flex-direction: column; gap: 2px; min-width: 0; text-align: left; background: none; border: 0; padding: 0; font: inherit; color: inherit; }
button.mc-fig { cursor: pointer; border-radius: 6px; }
button.mc-fig:hover .mc-num, button.mc-fig:focus-visible .mc-num { text-decoration: underline dotted; }
.mc .mc-lbl { font-size: var(--fs-sm, 13px); color: var(--mc-mute); font-weight: 600; }
.mc .mc-num { font-size: var(--fs-fig, 28px); line-height: 1.15; font-weight: 800; letter-spacing: -.01em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.mc .mc-num.sm { font-size: var(--fs-md, 15px); }
.mc .mc-num.unk { font-size: var(--fs-md, 15px); font-weight: 650; color: var(--mc-mute); letter-spacing: 0; line-height: 1.6; }
.mc .mc-num.out { color: var(--mc-bad); }
.mc .mc-line { font-size: var(--fs-sm, 13px); color: var(--mc-mute); margin: 0; }
.mc .mc-sub { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: var(--fs-sm, 13px); color: var(--mc-mute); min-height: 20px; }
.mc .mc-tag { display: inline-flex; gap: 4px; align-items: center; padding: 0 7px; border-radius: 999px; font-size: var(--fs-sm, 13px); font-weight: 650; border: 1px solid currentColor; white-space: nowrap; line-height: 18px; }
.mc .mc-tag.confirmed, .mc .mc-tag.ok { color: var(--mc-ok); }
.mc .mc-tag.assumed, .mc .mc-tag.warn { color: var(--mc-warn); }
.mc .mc-tag.contested, .mc .mc-tag.bad { color: var(--mc-bad); }
.mc .mc-tag.stale, .mc .mc-tag.unknown, .mc .mc-tag.none { color: var(--mc-mute); border-style: dashed; }
.mc .mc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.mc .mc-list > li { padding: 8px 0; border-bottom: 1px solid var(--mc-line); }
.mc .mc-list > li:last-child { border-bottom: 0; }
.mc .mc-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.mc .mc-row .grow { flex: 1 1 140px; min-width: 0; font-weight: 600; overflow-wrap: anywhere; }
.mc .mc-row .amt { font-weight: 750; font-variant-numeric: tabular-nums; }
.mc .mc-row .amt.unk { color: var(--mc-mute); font-weight: 600; }
.mc .mc-bar { height: 6px; border-radius: 3px; background: color-mix(in srgb, var(--mc-line) 70%, transparent); margin: 5px 0 2px; overflow: hidden; }
.mc .mc-bar > span { display: block; height: 100%; background: var(--mc-acc); opacity: .75; border-radius: 3px; }
.mc .mc-bar.stub { background: none; border-top: 1px dashed var(--mc-unk); height: 0; margin: 9px 0 5px; }
.mc .uncounted .grow, .mc .uncounted .amt { color: var(--mc-mute); }
.mc .uncounted .mc-bar > span { background: var(--mc-unk); }
.mc .mc-strip { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: color-mix(in srgb, var(--mc-line) 70%, transparent); }
.mc .mc-strip > span { display: block; height: 100%; min-width: 2px; }
.mc .mc-strip .gate { background: var(--mc-bad); opacity: .7; } .mc .mc-strip .lien, .mc .mc-strip .cost, .mc .mc-strip .fee { background: var(--mc-unk); }
.mc .mc-strip .cost { opacity: .75; } .mc .mc-strip .fee { opacity: .5; } .mc .mc-strip .net { background: var(--mc-acc); }
.mc .mc-note { font-size: var(--fs-sm, 13px); color: var(--mc-mute); margin: 2px 0 0; overflow-wrap: anywhere; }
.mc .mc-check { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: var(--fs-sm, 13px); padding: 6px 0 2px; }
.mc .mc-empty { color: var(--mc-mute); margin: 0; }
.mc .mc-read { margin: 0; padding: 8px 10px; border-left: 3px solid var(--mc-line); font-size: var(--fs-md, 15px); line-height: 1.5; }
.mc .mc-chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.mc .mc-btn { border: 1px solid var(--mc-line); background: var(--surface, #fff); color: inherit; padding: 4px 10px; border-radius: 8px; font: inherit; font-weight: 600; font-size: var(--fs-sm, 13px); cursor: pointer; }
.mc .mc-btn:hover:not(:disabled) { border-color: var(--mc-acc); color: var(--mc-acc); }
.mc .mc-btn:disabled { opacity: .55; cursor: default; }
.mc .mc-ctl { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.mc .mc-ctl input, .mc .mc-ctl select { font: inherit; padding: 4px 6px; border: 1px solid var(--mc-line); border-radius: 6px; background: var(--surface, #fff); color: inherit; }
.mc .mc-ctl input { width: 6em; }
.mc .mc-photo { width: 48px; height: 48px; border-radius: 10px; border: 1px solid var(--mc-line); overflow: hidden; display: grid; place-items: center; background: color-mix(in srgb, var(--mc-acc) 10%, transparent); color: var(--mc-acc); font-weight: 800; font-size: var(--fs-md, 15px); padding: 0; flex: none; }
.mc .mc-photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mc .mc-glance { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; }
.mc .mc-glance .mc-who { flex: 0 1 230px; min-width: 200px; }
.mc .mc-glance .mc-figs { flex: 1 1 640px; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px 12px; }
.mc .mc-who { display: flex; gap: 12px; align-items: center; }
.mc svg.mc-river { width: 100%; height: auto; display: block; }
.mc .mc-river .stage { cursor: pointer; } .mc .mc-river .stage:focus-visible { outline: none; }
.mc .mc-river .stage:hover .bar, .mc .mc-river .stage:focus-visible .bar { stroke: var(--mc-ink); stroke-width: 2; }
.mc .mc-river text { font-family: inherit; fill: var(--mc-ink); }
.mc .mc-river .ribbon { fill: var(--mc-acc); opacity: .2; } .mc .mc-river .ribbon.dim { opacity: .08; }
.mc .mc-river .ribbon.st-contested { fill: var(--mc-bad); opacity: .14; } .mc .mc-river .ribbon.st-assumed { fill: var(--mc-warn); opacity: .16; }
.mc .mc-river .ribbon.st-unknown, .mc .mc-river .ribbon.st-stale { fill: var(--mc-unk); opacity: .14; }
.mc .mc-river .bar.flow { fill: var(--mc-acc); } .mc .mc-river .st-confirmed .bar.flow { fill: var(--mc-ok); }
.mc .mc-river .st-assumed .bar.flow { fill: #c98a1b; } .mc .mc-river .st-contested .bar.flow { fill: var(--mc-bad); } .mc .mc-river .st-stale .bar.flow { fill: var(--mc-mute); }
.mc .mc-river .bar.divert { fill: #cfd5de; stroke: #9aa3b0; stroke-dasharray: 3 2; } .mc .mc-river .bar.divert.gate { fill: #fbd9d5; stroke: var(--mc-bad); }
.mc .mc-river .bar.unknown { fill: none; stroke: var(--mc-unk); stroke-dasharray: 4 3; stroke-width: 1.5; }
.mc .mc-river .q { fill: var(--mc-unk); font-weight: 700; font-size: var(--fs-md, 15px); }
.mc .mc-river .fall { fill: #9aa3b0; opacity: .35; } .mc .mc-river .fall.gate { fill: var(--mc-bad); opacity: .35; }
.mc .mc-river .head { font-size: var(--fs-md, 15px); font-weight: 750; } .mc .mc-river .lbl { font-size: var(--fs-sm, 13px); fill: var(--mc-mute); }
.mc .mc-river .amt { font-size: var(--fs-fig, 28px); font-weight: 800; letter-spacing: -.01em; } .mc .mc-river .amt.out, .mc .mc-river .amt.gate { fill: var(--mc-bad); }
.mc .mc-river .amt.unk { font-size: var(--fs-md, 15px); font-weight: 600; fill: var(--mc-mute); }
.mc .mc-river .div { font-size: var(--fs-sm, 13px); fill: var(--mc-mute); } .mc .mc-river .stat { font-size: var(--fs-sm, 13px); fill: var(--mc-mute); letter-spacing: .05em; }
.mc .mc-river .lock path { stroke: var(--mc-bad); } .mc .mc-river .lock rect { fill: var(--mc-bad); }
`;

let injected = false;
export function ensureStyle() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const s = document.createElement('style');
  s.id = 'mc-style';
  s.textContent = CSS;
  document.head.append(s);
}

// One money format everywhere: whole dollars when the cents are zero, cents only when they are not.
const usdWhole = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const usdCents = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const usd = (n) => (n == null ? null : Math.round(n * 100) % 100 === 0 ? usdWhole.format(n) : usdCents.format(n));

export const GLYPH = { confirmed: '✓', assumed: '~', contested: '⚑', stale: '◷', unknown: '?' };
const WORD = { confirmed: 'confirmed', assumed: 'assumed', contested: 'contested', stale: 'out of date', unknown: 'unknown' };

export function tag(ctx, status, text) {
  const s = status || 'unknown';
  return ctx.el('span', { class: `mc-tag ${s}`, text: `${GLYPH[s] || ''} ${text || WORD[s] || s}`.trim() });
}

export function wrap(ctx, ...kids) {
  ensureStyle();
  return ctx.el('div', { class: 'mc' }, ...kids);
}

export const empty = (ctx, text) => ctx.el('p', { class: 'mc-empty', text });

// A large figure with a small label. `amount == null` reads as unknown, never as 0.
export function fig(ctx, label, amount, { unknown = 'Not in the file', out = false, onclick, text, sm = false, sub, maxSrc = 2, refs } = {}) {
  const known = amount != null || text != null;
  const shown = text ?? (amount != null ? usd(amount) : null);
  const body = [
    ctx.el('span', { class: 'mc-lbl', text: label }),
    ctx.el('span', { class: `mc-num${known ? '' : ' unk'}${out && known ? ' out' : ''}${sm ? ' sm' : ''}`, text: known ? `${out ? '−' : ''}${shown}` : unknown }),
    sub || null,
  ];
  const own = refs || onclick?.refs;
  // the chips ride in the sub line, so the tile is no taller than before
  if (known) {
    const src = sources(ctx, own, maxSrc);
    if (sub) sub.append(src); else body.push(ctx.el('span', { class: 'mc-sub' }, src));
  }
  if (onclick && !own) return ctx.el('button', { class: 'mc-fig', type: 'button', onclick, title: 'Open the source' }, body);
  return ctx.el('div', { class: 'mc-fig' }, body);
}

// ---- the river's stages
export const stageOf = (c, kind) => (c.river?.stages || []).find((s) => s.kind === kind);
export const stagesOf = (c, ...kinds) => (c.river?.stages || []).filter((s) => kinds.includes(s.kind));
export const isUnknownStage = (c, s) => !s || (c.river?.unknown || []).includes(s.id);

// Sources for a stage: its own, else its node's.
export function stageRefs(c, s) {
  if (s?.sources?.length) return s.sources;
  const n = (c.nodes || []).find((x) => x.id === s?.node_id);
  return n?.sources || [];
}

export function openRefs(ctx, refs, title) {
  const list = (refs || []).filter(Boolean);
  const fn = list.length ? () => (list.length === 1 ? ctx.openSource(list[0]) : ctx.openSources(list, title)) : undefined;
  if (fn) fn.refs = list;
  return fn;
}

// The claim / source pattern: a claim carries its source chips on the same card. A document source names the
// document and page and opens it there; a record says so; no source says so. `max` chips, then "+N".
export function sources(ctx, refs, max = 2) {
  const list = (refs || []).filter(Boolean);
  if (!list.length) return ctx.el('span', { class: 'cs-none', text: 'no source in the file' });
  return ctx.el('span', { class: 'cs-sources' }, list.slice(0, max).map((r) => ctx.chip(r)),
    list.length > max ? ctx.el('span', { class: 'cs-more', title: `${list.length - max} more source${list.length - max === 1 ? '' : 's'}, listed in the expanded view`, text: `+${list.length - max}` }) : null);
}

// ---- the node tree
export function tree(c) {
  const kids = new Map();
  for (const n of c.nodes || []) {
    if (n.parent_id) { if (!kids.has(n.parent_id)) kids.set(n.parent_id, []); kids.get(n.parent_id).push(n); }
  }
  const checks = new Map();
  for (const k of c.river?.checks || []) checks.set((k.node_ids && k.node_ids[0]) ?? k.id, k);
  return { kids, checks, byId: new Map((c.nodes || []).map((n) => [n.id, n])) };
}

export function checkTag(ctx, check) {
  if (!check) return null;
  return check.ok ? tag(ctx, 'confirmed', 'adds up') : tag(ctx, 'contested', 'does not add up');
}

// One component band: label, amount, share-of-parent bar (geometry only), status, source.
export function band(ctx, n, parentAmount, t, depth = 0) {
  const w = n.amount != null && parentAmount > 0 ? Math.max(2, Math.min(100, (n.amount / parentAmount) * 100)) : 0;
  const children = t.kids.get(n.id) || [];
  const li = ctx.el('li', { class: n.counted === false ? 'uncounted' : '' },
    ctx.el('div', { class: 'mc-row' },
      ctx.el('span', { class: 'grow', text: n.label }),
      ctx.el('span', { class: `amt${n.amount == null ? ' unk' : ''}`, text: n.amount != null ? usd(n.amount) : 'not in the file' }),
      n.counted === false ? tag(ctx, 'stale', 'adds nothing') : null,
      tag(ctx, n.status || 'unknown'),
      sources(ctx, n.sources, 3)),
    n.amount != null ? ctx.el('div', { class: 'mc-bar' }, ctx.el('span', { style: `width:${w}%` })) : ctx.el('div', { class: 'mc-bar stub' }),
    n.basis ? ctx.el('p', { class: 'mc-note', text: n.basis }) : null,
    n.payer && n.payer !== n.label ? ctx.el('p', { class: 'mc-note', text: `Payer: ${n.payer}` }) : null);
  if (children.length && depth < 3) li.append(bandList(ctx, n, children, t, depth + 1));
  return li;
}

export function bandList(ctx, parent, children, t, depth = 0) {
  const check = t.checks.get(parent.id);
  return ctx.el('div', {},
    check ? ctx.el('div', { class: 'mc-check' }, checkTag(ctx, check), check.ok ? null : ctx.el('span', { class: 'mc-note', text: check.label })) : null,
    ctx.el('ul', { class: 'mc-list' }, children.map((n) => band(ctx, n, parent.amount ?? 0, t, depth))));
}

// ---- value river SVG (geometry only), ported from the main interface
const W = 880, H = 372, PAD_X = 84, BAR_W = 30, TOP = 84, ZONE = 190;
const MID = TOP + ZONE / 2, AMT_Y = TOP + ZONE + 46;
const DEDUCT = new Set(['gate', 'lien', 'cost', 'fee']);
const HEAD = {
  value: 'What the case is worth', economic: 'What the case is worth', coverage: 'What can pay it',
  gate: 'Held back', lien: 'Liens come off', cost: 'Costs come off', fee: 'Fee comes off', net: 'What is left',
};

function wrapLines(s, n) {
  const words = String(s || '').split(/\s+/).filter(Boolean);
  const lines = [''];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    if (!cur || (cur + ' ' + w).length <= n) lines[lines.length - 1] = cur ? `${cur} ${w}` : w;
    else if (lines.length < 2) lines.push(w);
    else lines[1] = `${lines[1]} ${w}`;
  }
  return lines.map((l) => (l.length > n + 4 ? `${l.slice(0, n + 1)}...` : l));
}

export function riverSvg(ctx, river, onOpen) {
  const stages = river?.stages || [];
  const unknown = new Set(river.unknown || []);
  const money = usd;
  const feeStage = stages.find((t) => t.kind === 'fee');
  const feeUnset = !feeStage || unknown.has(feeStage.id) || feeStage.diverted == null;
  const knownIn = stages.map((s) => s.inflow).filter((v) => v != null);
  const max = knownIn.length ? Math.max(...knownIn) : 0;
  const k = max > 0 ? ZONE / max : 0;
  const n = stages.length;
  const x = (i) => PAD_X + i * ((W - 2 * PAD_X - BAR_W) / Math.max(n - 1, 1));
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'mc-river', role: 'group', 'aria-label': 'Value river' });
  const ribbons = svgEl('g'), falls = svgEl('g'), bars = svgEl('g');
  svg.append(ribbons, falls, bars);
  let carry = 0;
  const geo = stages.map((s) => {
    const isUnknown = unknown.has(s.id) || s.inflow == null;
    const inH = s.inflow != null ? s.inflow * k : carry;
    const outH = s.outflow != null ? s.outflow * k : inH;
    const divH = s.diverted != null ? s.diverted * k : 0;
    carry = outH;
    return { s, isUnknown, inH, outH, divH, top: MID - Math.max(inH, 0) / 2 };
  });
  geo.forEach((g, i) => {
    const { s } = g;
    const bx = x(i), x0 = bx + BAR_W;
    if (i + 1 < n) {
      const nxt = geo[i + 1];
      const x1 = x(i + 1), xm = (x0 + x1) / 2;
      const y0 = g.top, h0 = g.outH, y1 = nxt.top, h1 = nxt.inH;
      ribbons.append(svgEl('path', {
        class: `ribbon st-${s.status}${g.isUnknown || nxt.isUnknown ? ' dim' : ''}`,
        d: `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + h1} C${xm},${y1 + h1} ${xm},${y0 + h0} ${x0},${y0 + h0} Z`,
      }));
    }
    if (g.divH > 0 && !g.isUnknown) {
      const yd = g.top + g.outH, run = 56, drop = 16, th = Math.max(g.divH * 0.4, 3);
      falls.append(svgEl('path', {
        class: `fall ${s.kind}`,
        d: `M${x0},${yd} C${x0 + run / 2},${yd} ${x0 + run / 2},${yd + drop} ${x0 + run},${yd + drop} L${x0 + run},${yd + drop + th} C${x0 + run / 2},${yd + drop + th} ${x0 + run / 2},${yd + g.divH} ${x0},${yd + g.divH} Z`,
      }));
    }
    const grp = svgEl('g', {
      class: `stage ${s.kind} st-${s.status}${g.isUnknown ? ' is-unknown' : ''}`, tabindex: 0, role: 'button',
      'aria-label': `${HEAD[s.kind] || s.kind}: ${s.label}${s.inflow != null ? `, ${money(s.outflow ?? s.inflow)}` : ', not in the file'}. Open source.`,
    });
    if (g.isUnknown) {
      grp.append(svgEl('rect', { class: 'bar unknown', x: bx, y: MID - 22, width: BAR_W, height: 44 }));
      grp.append(svgEl('text', { class: 'q', x: bx + BAR_W / 2, y: MID + 6, 'text-anchor': 'middle' }, '?'));
    } else {
      grp.append(svgEl('rect', { class: 'bar flow', x: bx, y: g.top, width: BAR_W, height: Math.max(g.outH, 2) }));
      if (g.divH > 0) grp.append(svgEl('rect', { class: `bar divert ${s.kind}`, x: bx, y: g.top + g.outH, width: BAR_W, height: Math.max(g.divH, 2) }));
    }
    const cx = bx + BAR_W / 2;
    grp.append(svgEl('text', { class: 'head', x: cx, y: 18, 'text-anchor': 'middle' }, HEAD[s.kind] || s.kind));
    const shownLabel = s.kind === 'net' && feeUnset ? 'After liens and costs, before fee' : s.label;
    const label = svgEl('text', { class: 'lbl', x: cx, y: 36, 'text-anchor': 'middle' });
    wrapLines(shownLabel, 20).forEach((ln, j) => label.append(svgEl('tspan', { x: cx, dy: j ? 13 : 0 }, ln)));
    label.append(svgEl('title', {}, shownLabel));
    grp.append(label);
    const deduct = DEDUCT.has(s.kind);
    const head = deduct ? s.diverted : (s.outflow ?? s.inflow);
    const unknownHead = g.isUnknown || head == null;
    grp.append(svgEl('text', {
      class: `amt ${s.kind}${deduct && !unknownHead ? ' out' : ''}${unknownHead ? ' unk' : ''}`, x: cx, y: AMT_Y, 'text-anchor': 'middle',
    }, unknownHead ? (s.kind === 'gate' ? 'not yet confirmed' : s.kind === 'fee' ? 'fee not set' : 'not in the file') : `${deduct ? '−' : ''}${money(head)}`));
    let y = AMT_Y + 22;
    if (deduct && !unknownHead && s.outflow != null) {
      grp.append(svgEl('text', { class: 'div', x: cx, y, 'text-anchor': 'middle' }, `${money(s.outflow)} passes on`));
      y += 20;
    }
    grp.append(svgEl('text', { class: 'stat', x: cx, y, 'text-anchor': 'middle' }, `${GLYPH[s.status] || ''} ${s.status}`));
    if (s.kind === 'net' && feeUnset) grp.append(svgEl('text', { class: 'div', x: cx, y: y + 18, 'text-anchor': 'middle' }, 'fee not applied'));
    const open = () => onOpen(s);
    grp.addEventListener('click', open);
    grp.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    bars.append(grp);
  });
  return svg;
}

// ---- a header fact as a figure: the server's display string, its status in words, a button to its source
export function factFig(ctx, label, fact, { statusOverride, big = true, compact = false } = {}) {
  const refs = fact?.sources || [];
  if (!fact) return fig(ctx, label, null, { sm: !big, unknown: 'Not in the record' });
  const status = statusOverride || fact.status;
  const n = fact.conflict_ids?.length || 0;
  // On a card face: one pill per figure, every figure treated alike; the review count rides in the tooltip and shows in the detail.
  const sub = compact
    ? ctx.el('span', { class: 'mc-sub', title: n ? `${n} difference${n === 1 ? '' : 's'} for review` : undefined }, status && status !== 'unknown' ? tag(ctx, status) : null)
    : ctx.el('span', { class: 'mc-sub' },
      status && status !== 'unknown' ? tag(ctx, status) : null,
      n ? tag(ctx, 'assumed', `${n} to review`) : null,
      fact.date ? ctx.el('span', { text: ctx.fmt.date(fact.date) }) : null);
  return fig(ctx, label, null, { text: (fact.amount != null ? usd(fact.amount) : fact.display) || 'Not in the file', sm: !big, sub, refs, maxSrc: compact ? 1 : 99 });
}

// Facts a lawyer can bind a single-figure card to: the header facts that are one sourced value.
export function headerFacts(c) {
  return Object.entries(c.brief || {})
    .filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v) && 'display' in v && 'derivation' in v)
    .map(([key, f]) => ({ value: key, label: f.label || key.replace(/_/g, ' ') }));
}

