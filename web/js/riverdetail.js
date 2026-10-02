// River v2 detail under the SVG: how the value is made up (tributaries, per-provider bands), which coverage
// layers count, the server's sum checks and its reading line. Everything printed is the server's; the only
// arithmetic-looking thing is a bar's width, which is geometry (this component against its parent).
import { el, fmtMoney, pill, statusPill } from './util.js';
import { chip } from './drawer.js';

const openGroups = new Set();   // survives live refresh, so a group the user opened stays open

export function adaptRiver(c) {
  const river = c.river || {};
  const kids = new Map();
  for (const n of c.nodes || []) {
    if (n.parent_id) { if (!kids.has(n.parent_id)) kids.set(n.parent_id, []); kids.get(n.parent_id).push(n); }
  }
  const checks = new Map();
  for (const k of river.checks || []) {
    const key = (k.node_ids && k.node_ids[0]) ?? k.stage_id ?? k.node_id ?? k.id;
    checks.set(key, { ok: !!(k.ok ?? k.passed), label: k.label || k.message || '', expected: k.expected, actual: k.actual });
  }
  const roots = (c.nodes || []).filter((n) => !n.parent_id && kids.has(n.id));
  return { kids, checks, roots, reading: river.reading || null, share: river.evidence_backed_share ?? null };
}

function row(n, parentAmount, ctx) {
  const w = n.amount != null && parentAmount > 0 ? Math.max(2, Math.min(100, (n.amount / parentAmount) * 100)) : 0;   // geometry only
  const children = ctx.kids.get(n.id) || [];
  const head = el('div', { class: 'comp-h' },
    el('span', { class: 'comp-l', text: n.label }),
    el('span', { class: 'comp-a', text: n.amount != null ? fmtMoney(n.amount) : 'not in the file' }),
    n.counted === false ? pill('adds nothing', 'st-stale') : null,
    n.status && n.status !== 'unknown' ? statusPill(n.status) : pill('unknown', 'st-unknown'),
    n.sources?.[0] ? chip(n.sources[0]) : null);
  const bar = n.amount != null ? el('div', { class: 'comp-bar' }, el('span', { style: `width:${w}%` })) : el('div', { class: 'comp-bar stub' });
  const sub = [n.basis, n.payer ? `Payer: ${n.payer}` : null].filter(Boolean);
  if (!children.length) return el('li', { class: `comp${n.counted === false ? ' uncounted' : ''}` }, head, bar, ...sub.map((t) => el('div', { class: 'small muted', text: t })));
  return el('li', { class: 'comp' }, head, bar, ...sub.map((t) => el('div', { class: 'small muted', text: t })), group(n, children, ctx, false));
}

function group(parent, children, ctx, withCheck) {
  const isOpen = openGroups.has(parent.id);
  const check = ctx.checks.get(parent.id);
  const body = el('ul', { class: 'comps', hidden: !isOpen }, children.map((n) => row(n, parent.amount ?? 0, ctx)));
  const toggle = el('button', { class: 'comp-toggle', type: 'button', 'aria-expanded': String(isOpen) },
    el('span', { class: 'caret', 'aria-hidden': 'true', text: isOpen ? '▾' : '▸' }),
    el('strong', { text: withCheck ? parent.label : `Break down: ${children.length} item${children.length === 1 ? '' : 's'}` }),
    el('span', { class: 'muted small', text: withCheck ? `${children.length} component${children.length === 1 ? '' : 's'}` : '' }),
    check ? (check.ok ? pill('✓ adds up', 'ok') : pill('⚑ does not add up', 'warn')) : null);
  toggle.addEventListener('click', () => {
    const now = body.hidden;
    body.hidden = !now;
    toggle.setAttribute('aria-expanded', String(now));
    toggle.querySelector('.caret').textContent = now ? '▾' : '▸';
    if (now) openGroups.add(parent.id); else openGroups.delete(parent.id);
  });
  return el('div', { class: 'stage-detail' }, toggle, check && !check.ok ? el('p', { class: 'small', text: check.label }) : null, body);
}

export function riverDetail(c) {
  const ctx = adaptRiver(c);
  const groups = ctx.roots.map((n) => group(n, ctx.kids.get(n.id), ctx, true));
  if (!ctx.reading && !groups.length) return el('div', { hidden: true });
  return el('div', { class: 'river-detail' },
    ctx.reading ? el('p', { class: 'reading', title: ctx.share != null ? `${Math.round(ctx.share * 100)}% of the case value has a document on file behind it.` : '', text: ctx.reading }) : null,
    ...groups);
}
