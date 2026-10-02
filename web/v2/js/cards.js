// Card registry, rendering guard and the generic custom card.
// See ../cards/README.md for the contract. Nothing here knows about a particular card.
import { el, fmtMoney, fmtDate, fmtDateTime, dayWords, pill } from '../../js/util.js';
import { api } from '../../js/api.js';
import { chip, chips, openSources } from '../../js/drawer.js';

export const GROUPS = ['Case', 'Money', 'Tasks', 'Documents', 'Review', 'People', 'Communications', 'Providers', 'Activity'];
export const SIZES = { s: 1, m: 2, l: 3 };
const MANIFESTS = ['a', 'b', 'c', 'd'];

export const registry = { cards: new Map(), problems: [], loaded: false };

function validate(card, from) {
  const errs = [];
  if (!card || typeof card !== 'object') return ['not an object'];
  if (typeof card.id !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(card.id)) errs.push('id must be kebab-case');
  if (typeof card.title !== 'string' || !card.title) errs.push('title missing');
  if (!(card.size in SIZES)) errs.push("size must be 's', 'm' or 'l'");
  if (typeof card.summary !== 'function') errs.push('summary() missing');
  if (card.detail != null && typeof card.detail !== 'function') errs.push('detail must be a function');
  if (card.empty != null && typeof card.empty !== 'function') errs.push('empty must be a function');
  if (card.template && !Array.isArray(card.settings)) errs.push('a template needs settings: [{key,label,options(caseModel)}]');
  if (!Array.isArray(card.depends)) errs.push('depends must be an array');
  if (card.group && !GROUPS.includes(card.group)) errs.push(`unknown group "${card.group}"`);
  return errs.map((e) => `${from}: ${card?.id ?? '?'}: ${e}`);
}

export async function loadCards() {
  if (registry.loaded) return registry;
  registry.loaded = true;
  for (const set of MANIFESTS) {
    let list = [];
    try { list = (await import(`../cards/${set}.manifest.js`)).default; } catch (err) { registry.problems.push(`${set}.manifest.js: ${err.message}`); continue; }
    if (!Array.isArray(list)) { registry.problems.push(`${set}.manifest.js: default export is not an array`); continue; }
    for (const card of list) {
      const errs = validate(card, `${set}.manifest.js`);
      if (!errs.length && registry.cards.has(card.id)) errs.push(`${set}.manifest.js: ${card.id}: duplicate id`);
      if (errs.length) { registry.problems.push(...errs); continue; }
      registry.cards.set(card.id, { group: 'Case', ...card });
    }
  }
  if (registry.problems.length) console.warn('card problems', registry.problems);
  return registry;
}

// ---- the context handed to every card

export function makeCtx({ matterId, openTab, openWrite, toast, reload }) {
  return {
    el, chip, chips, pill, toast, matterId, api, reload,
    openSource: (ref) => openSources([ref]),
    openSources,
    openTab, openWrite,
    fmt: {
      money: fmtMoney, date: fmtDate, dateTime: fmtDateTime, days: dayWords,
      plural: (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`,
    },
  };
}

// ---- rendering guard: a card can be empty, missing data, or broken, and none of that breaks the page

const present = (c, key) => c[key] !== undefined;

export function renderCard(card, c, ctx, part = 'summary') {
  try {
    const missing = (card.depends || []).filter((k) => !present(c, k));
    if (missing.length) return { node: null, state: 'hidden', reason: `needs ${missing.join(', ')}` };
    if (part === 'summary') {
      const why = card.empty?.(c, card.settings || {});
      if (why) return { node: el('p', { class: 'v2-empty', text: why }), state: 'empty' };
    }
    const fn = part === 'detail' ? card.detail : card.summary;
    if (!fn) return { node: null, state: 'none' };
    const node = fn.call(card, c, ctx, card.settings || {});
    if (!node || !node.nodeType) return { node: el('p', { class: 'v2-card-error', text: 'This card returned nothing to show.' }), state: 'error' };
    return { node, state: 'ok' };
  } catch (err) {
    console.error(`card "${card.id}" failed`, err);
    return { node: el('p', { class: 'v2-card-error', text: `This card could not be drawn (${err.message}).` }), state: 'error' };
  }
}

// ---- the custom card: data, not code

export const ALLOWED_SOURCES = [
  'brief.case_value', 'brief.coverage', 'brief.firm_spend', 'brief.last_client_contact', 'brief.limitations', 'brief.stage',
  'agenda.overdue', 'agenda.coming', 'agenda.waiting', 'timeline', 'conflicts', 'moves', 'documents', 'providers', 'spend.lines',
  'custom_fields', 'key_facts', 'incoming', 'changes.items', 'contacts',
];
const KINDS = ['stat', 'list', 'table', 'timeline', 'text'];
const FIELD_RE = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$/;

export function validateSpec(spec) {
  const errors = [];
  if (!spec || typeof spec !== 'object') return { ok: false, errors: ['spec is not an object'], spec: null };
  const out = { kind: spec.kind, title: String(spec.title || '').slice(0, 80), source: spec.source };
  if (!KINDS.includes(spec.kind)) errors.push(`kind must be one of ${KINDS.join(', ')}`);
  if (!out.title) errors.push('title missing');
  if (spec.kind === 'text') {
    out.text = String(spec.text || '').slice(0, 280);
    if (!out.text) errors.push('text missing');
    delete out.source;
  } else {
    if (!ALLOWED_SOURCES.includes(spec.source)) errors.push(`source "${spec.source}" is not allowed`);
    if (spec.kind === 'stat') {
      out.stat = spec.stat === 'value' ? 'value' : 'count';
    } else {
      const fields = Array.isArray(spec.fields) ? spec.fields.slice(0, 5) : [];
      if (!fields.length) errors.push('fields missing');
      for (const f of fields) if (typeof f !== 'string' || !FIELD_RE.test(f)) errors.push(`field "${f}" is not a plain property name`);
      out.fields = fields;
      const lim = Number(spec.limit ?? 5);
      out.limit = Math.max(1, Math.min(25, Number.isFinite(lim) ? Math.floor(lim) : 5));
      if (spec.filter) {
        const f = spec.filter;
        if (f.field && FIELD_RE.test(f.field) && ['eq', 'contains', 'gt', 'lt'].includes(f.op) && f.value != null) out.filter = { field: f.field, op: f.op, value: String(f.value).slice(0, 80) };
        else errors.push('filter must be {field, op: eq|contains|gt|lt, value}');
      }
      if (spec.sort) {
        if (spec.sort.field && FIELD_RE.test(spec.sort.field)) out.sort = { field: spec.sort.field, dir: spec.sort.dir === 'desc' ? 'desc' : 'asc' };
        else errors.push('sort.field is not a plain property name');
      }
    }
  }
  return { ok: errors.length === 0, errors, spec: errors.length ? null : out };
}

const getPath = (obj, path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
const asList = (v) => (Array.isArray(v) ? v : v == null ? [] : [v]);

function fieldText(item, field, ctx) {
  const v = getPath(item, field);
  if (v == null || v === '') return '';
  const leaf = field.split('.').pop();
  if (typeof v === 'number' && /amount|total|value/.test(leaf)) return ctx.fmt.money(v) ?? '';
  if (typeof v === 'string' && (/(^|_)(due|date)$/.test(leaf) || /_at$/.test(leaf))) return ctx.fmt.date(v) ?? v;
  return typeof v === 'object' ? '' : String(v);
}

function sourceOf(item) {
  return item?.source?.href ? item.source : (item?.sources?.[0]?.href ? item.sources[0] : null);
}

function filterItems(items, f) {
  if (!f) return items;
  const want = String(f.value).toLowerCase();
  return items.filter((it) => {
    const v = getPath(it, f.field);
    if (v == null) return false;
    const s = String(v).toLowerCase();
    if (f.op === 'eq') return s === want;
    if (f.op === 'contains') return s.includes(want);
    const a = Number.isNaN(Number(v)) ? String(v) : Number(v), b = Number.isNaN(Number(f.value)) ? String(f.value) : Number(f.value);
    return f.op === 'gt' ? a > b : a < b;   // dates compare as ISO text
  });
}

function sortItems(items, sort) {
  if (!sort) return items;
  const k = sort.field, dir = sort.dir === 'desc' ? -1 : 1;
  return [...items].sort((a, b) => {
    const x = getPath(a, k), y = getPath(b, k);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
}

export function customCard(spec) {
  const { ok, errors, spec: s } = validateSpec(spec);
  if (!ok) return { error: errors };
  const rootKey = s.source ? s.source.split('.')[0] : null;
  return {
    id: `custom-${s.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'card'}`,
    title: s.title, group: 'Case', size: s.kind === 'table' ? 'l' : s.kind === 'stat' ? 's' : 'm', depends: rootKey ? [rootKey] : [],
    custom: true, spec: s,
    summary(c, ctx) {
      if (s.kind === 'text') return el('p', { class: 'v2-text', text: s.text });
      const raw = getPath(c, s.source);
      const items = filterItems(asList(raw), s.filter);
      if (s.kind === 'stat') {
        if (s.stat === 'value' && raw && !Array.isArray(raw)) {
          const ref = raw.sources?.[0];
          return el('div', { class: 'v2-stat' }, el('div', { class: 'v2-stat-v', text: raw.display ?? '-' }), ref ? ctx.chip(ref) : null);
        }
        return el('div', { class: 'v2-stat' }, el('div', { class: 'v2-stat-v', text: String(items.length) }), el('div', { class: 'muted small', text: ctx.fmt.plural(items.length, 'item').replace(/^\d+ /, '') }));
      }
      if (!items.length) return el('p', { class: 'v2-empty', text: 'Nothing here yet.' });
      const rows = sortItems(items, s.sort).slice(0, s.limit);
      if (s.kind === 'table') {
        return el('table', { class: 'v2-table' }, el('thead', {}, el('tr', {}, s.fields.map((f) => el('th', { text: f.replace(/[._]/g, ' ') })))),
          el('tbody', {}, rows.map((r) => el('tr', {}, s.fields.map((f, i) => el('td', {}, fieldText(r, f, ctx), i === s.fields.length - 1 && sourceOf(r) ? ctx.chip(sourceOf(r)) : null))))));
      }
      return el('ul', { class: `v2-list${s.kind === 'timeline' ? ' timeline' : ''}` }, rows.map((r) => el('li', {},
        ...s.fields.map((f, i) => el('span', { class: i === 0 ? 'v2-li-main' : 'muted small', text: fieldText(r, f, ctx) })),
        sourceOf(r) ? ctx.chip(sourceOf(r)) : null)));
    },
  };
}
