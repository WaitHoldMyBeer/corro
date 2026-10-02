// Turns a normalised answer (see client.js) into DOM. Everything the assistant or a document says
// reaches the page through textContent; nothing here builds HTML from text.
import { el, svgEl, fmtMoney } from '../../../js/util.js';

export const fmtMs = (ms) => (ms == null ? null : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`);
export const fmtUsd = (u) => (u == null ? null : u === 0 ? '$0.00' : u < 0.001 ? 'under $0.001' : u < 0.1 ? `$${u.toFixed(3)}` : `$${u.toFixed(2)}`);
const fmtTok = (n) => (n == null ? null : n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`);
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const dot = (...parts) => parts.filter(Boolean).join(' · ');

export function graphIcon() {
  const s = svgEl('svg', { viewBox: '0 0 16 16', width: 13, height: 13, 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round' });
  s.append(svgEl('path', { d: 'M5.2 4.6 10.6 6.2M10.9 8.1 7 11.3M4.3 5.8 5.4 10.6' }), svgEl('circle', { cx: 3.8, cy: 4, r: 1.8 }), svgEl('circle', { cx: 12.2, cy: 6.6, r: 1.8 }), svgEl('circle', { cx: 5.8, cy: 12.2, r: 1.8 }));
  return s;
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through to the old way */ }
  const ta = el('textarea', { class: 'as-offscreen', 'aria-hidden': 'true' });
  ta.value = text; document.body.append(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  return ok;
}

// ---------------------------------------------------------------- citations

export const citeName = (c) => `${c.label}${c.page ? `, p. ${c.page}` : ''}`;

// One source: the name opens it in the drawer, the small button beside it lights it in the graph.
export function citeChip(c, ctx) {
  const wrap = el('span', { class: 'as-cite' });
  const inner = [el('span', { class: 'as-cite-l', text: c.label }), c.page ? el('span', { class: 'as-cite-p', text: `p. ${c.page}` }) : null];
  wrap.append(c.ref
    ? el('button', { type: 'button', class: 'as-cite-open', title: `Open the source: ${citeName(c)}`, onclick: () => ctx.openRef(c.ref) }, inner)
    : el('span', { class: 'as-cite-open as-cite-static', title: citeName(c) }, inner));
  if (c.nodeId) {
    wrap.append(el('button', { type: 'button', class: 'as-cite-g', title: 'Show in graph', 'aria-label': `Show ${citeName(c)} in the graph`, onclick: () => ctx.showInGraph([c]) }, graphIcon()));
  }
  return wrap;
}

// The sources of one sentence or entry. Past `most`, the rest wait behind a "+N" so a sentence stays readable.
function citeRow(list, ctx, most = 6) {
  if (!list.length) return null;
  const row = el('span', { class: 'as-cites' }, list.slice(0, most).map((c) => citeChip(c, ctx)));
  if (list.length > most) {
    const more = el('button', { type: 'button', class: 'as-cite-more', title: 'Show the other sources', text: `+${list.length - most}` });
    more.addEventListener('click', () => more.replaceWith(...list.slice(most).map((c) => citeChip(c, ctx))));
    row.append(more);
  }
  return row;
}

const NIF = 'not in the file';
const UNV = 'This figure was not found in the sources the sentence cites.';

// The sentence's text, with each figure the server could not find in the cited sources underlined in place.
// Returns the nodes and the figures that do not appear in the text as written.
export function marked(text, figures) {
  const out = [], missing = [], figs = (figures || []).filter(Boolean);
  let rest = text;
  for (const f of figs) if (!text.includes(f)) missing.push(f);
  for (;;) {
    let at = -1, hit = null;
    for (const f of figs) { const i = rest.indexOf(f); if (i >= 0 && (at < 0 || i < at)) { at = i; hit = f; } }
    if (!hit) break;
    if (at) out.push(rest.slice(0, at));
    out.push(el('span', { class: 'as-unv', title: UNV, text: hit }));
    rest = rest.slice(at + hit.length);
  }
  if (rest) out.push(rest);
  return { nodes: out, missing };
}

function sentence(s, ctx) {
  const m = marked(s.text, s.unverified);
  return [
    el('span', { class: `as-sent${s.notInFile ? ' nif' : ''}`, title: s.notInFile ? 'No page or record in the file supports this sentence.' : null }, m.nodes),
    s.notInFile ? el('span', { class: 'as-nif', text: NIF }) : null,
    m.missing.length ? el('span', { class: 'as-nif warn', title: UNV, text: `not found in its sources: ${m.missing.join(', ')}` }) : null,
    s.cites.length ? [' ', citeRow(s.cites, ctx, 2)] : null, ' ',
  ];
}

// One citation per graph node, in the order first cited.
export const nodes = (list) => { const seen = new Set(); return list.filter((c) => c.nodeId && !seen.has(c.nodeId) && seen.add(c.nodeId)); };

export function blockCites(b) {
  if (b.type === 'paragraph') return b.sentences.flatMap((s) => s.cites);
  if (b.type === 'table') return b.rows.flatMap((r) => r.cites);
  if (b.type === 'document') return b.entries.flatMap((e) => e.cites);
  return [];
}

// ---------------------------------------------------------------- table

function tableBlock(b, ctx) {
  const sourced = b.rows.some((r) => r.cites.length);
  const table = el('table', { class: 'v2-table as-table' },
    el('thead', {}, el('tr', {}, b.columns.map((c) => el('th', { scope: 'col', text: c })), sourced ? el('th', { scope: 'col', text: 'Source' }) : null)),
    el('tbody', {}, b.rows.map((r) => el('tr', {}, r.cells.map((c) => el('td', { class: /^[$-]?\d[\d,.\s%]*$/.test(c) ? 'num' : null, text: c })), sourced ? el('td', {}, citeRow(r.cites, ctx)) : null))));
  const copy = el('button', { type: 'button', class: 'btn quiet sm', text: 'Copy' });
  copy.addEventListener('click', async () => {
    const text = [[...b.columns, 'Source'].join('\t'), ...b.rows.map((r) => [...r.cells, r.cites.map(citeName).join('; ')].join('\t'))].join('\n');
    ctx.toast(await copyText(text) ? 'Table copied.' : 'Could not copy.');
  });
  const lit = nodes(b.rows.flatMap((r) => r.cites));
  const graphBtn = lit.length ? el('button', { type: 'button', class: 'btn quiet sm', onclick: () => ctx.showInGraph(lit) }, graphIcon(), 'Show in graph') : null;
  return el('section', { class: 'as-doc as-tabledoc' },
    el('header', { class: 'as-doc-h' }, el('div', { class: 'as-doc-t' }, el('h3', { text: b.title || 'Table' }), el('span', { class: 'as-doc-meta', text: plural(b.rows.length, 'row') })), el('div', { class: 'as-doc-act' }, graphBtn, copy)),
    el('div', { class: 'as-tablewrap', tabindex: '0', role: 'region', 'aria-label': b.title || 'Table' }, table));
}

// ---------------------------------------------------------------- documents

const MONTH = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const monthOf = (d) => (/^\d{4}-\d{2}/.test(d || '') ? d.slice(0, 7) : '');
const monthLabel = (ym) => (ym ? MONTH.format(new Date(`${ym}-01T00:00:00Z`)) : 'No date in the source');
const span = (a, b) => (a && b && a !== b ? `${a} to ${b}` : (a || b || null));

// A document the assistant assembled. A chronology is a rail of months; the other kinds are grouped
// as the server grouped them. Amounts and totals are printed as sent, never added here.
export function documentBlock(b, ctx) {
  const byMonth = b.kind === 'medical_chronology' || !b.entries.some((e) => e.group);
  const entries = b.entries.map((e, i) => ({ ...e, i }));
  if (byMonth) entries.sort((x, y) => (x.date || '9999').localeCompare(y.date || '9999') || x.i - y.i);
  // One provider can arrive under several spellings (punctuation, case, "M.D." or "MD"): the filter treats them as one.
  const provKey = (p) => String(p || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  const provName = new Map();
  for (const e of entries) { e.pk = provKey(e.provider); if (e.pk && !provName.has(e.pk)) provName.set(e.pk, e.provider); }
  const providers = [...provName.keys()];
  const dated = entries.map((e) => e.date).filter(Boolean).sort();
  const groupInfo = new Map(b.groups.map((g) => [g.label, g]));
  let filter = '';
  const shown = () => entries.filter((e) => !filter || e.pk === filter);

  const body = el('ol', { class: 'as-chrono' });
  const meta = el('span', { class: 'as-doc-meta' });
  // The years at a glance: how much the file holds for each, and a way to jump there.
  const years = byMonth ? el('div', { class: 'as-years', role: 'group', 'aria-label': 'Jump to a year' }) : null;
  function paintYears(list) {
    const per = new Map();
    for (const e of list) if (e.date) per.set(e.date.slice(0, 4), (per.get(e.date.slice(0, 4)) || 0) + 1);
    years.hidden = per.size < 2;
    const most = Math.max(1, ...per.values());
    years.replaceChildren(...[...per].map(([y, n]) => el('button', { type: 'button', class: 'as-year', title: `${plural(n, 'entry', 'entries')} in ${y}`,
      onclick: () => body.querySelector(`[data-ym^="${y}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }) },
    el('span', { class: 'as-year-y', text: y }), el('span', { class: 'as-year-n', text: String(n) }), el('i', { class: 'as-year-b', style: `width:${Math.max(6, Math.round((n / most) * 100))}%` }))));
  }
  function paint() {
    const list = shown();
    if (years) paintYears(list);
    meta.textContent = dot(plural(list.length, 'entry', 'entries'), !filter && providers.length ? plural(providers.length, 'provider') : null, span(dated[0], dated[dated.length - 1]),
      !filter && b.totals.amount != null ? `total ${fmtMoney(b.totals.amount)}` : null);
    const stops = new Map();
    for (const e of list) { const k = byMonth ? monthOf(e.date) : e.group; if (!stops.has(k)) stops.set(k, []); stops.get(k).push(e); }
    body.replaceChildren(...[...stops].map(([key, rows]) => {
      const g = byMonth ? null : groupInfo.get(key);
      return el('li', { class: 'as-month', 'data-ym': byMonth ? key : null },
        el('h4', { class: 'as-month-h' }, el('span', { text: byMonth ? monthLabel(key) : (key || 'Other') }), el('span', { class: 'as-month-n', text: String(rows.length) }),
          g && !filter ? el('span', { class: 'as-month-x', text: dot(span(g.first, g.last), g.total != null ? fmtMoney(g.total) : null) }) : null),
        el('ol', { class: 'as-entries' }, rows.map((e) => el('li', { class: 'as-entry' },
          el('div', { class: 'as-when' }, e.date ? el('time', { class: 'as-date', datetime: e.date, text: e.date }) : el('span', { class: 'as-date none', text: 'Undated' }), e.dateKind ? el('span', { class: 'as-datekind', text: e.dateKind }) : null),
          el('div', { class: 'as-what' }, e.provider ? el('span', { class: 'as-prov' }, e.provider, e.providerPrinted ? el('span', { class: 'as-prov-p', text: `printed as ${e.providerPrinted}` }) : null) : null,
            el('p', { class: 'as-what-t' }, el('span', { class: 'as-sent', text: e.text }), e.amount != null ? el('span', { class: 'as-amt', text: fmtMoney(e.amount) }) : null,
              e.cites.length ? null : el('span', { class: 'as-nif', text: NIF })),
            citeRow(e.cites, ctx))))));
    }));
    if (!list.length) body.replaceChildren(el('li', { class: 'as-none', text: 'No entries for this provider.' }));
  }

  // A few providers are buttons; many are a list to choose from.
  let filterRow = null;
  const count = (p) => entries.filter((e) => e.pk === p).length;
  if (providers.length > 1 && providers.length <= 4) {
    filterRow = el('div', { class: 'as-filter', role: 'group', 'aria-label': 'Show one provider' });
    const paintFilter = () => filterRow.replaceChildren(...[['', 'All providers', entries.length], ...providers.map((p) => [p, provName.get(p), count(p)])].map(([value, label, n]) =>
      el('button', { type: 'button', class: 'as-fbtn', 'aria-pressed': filter === value ? 'true' : 'false', onclick: () => { filter = value; paintFilter(); paint(); } },
        el('span', { class: 'as-fbtn-l', text: label }), el('span', { class: 'as-fbtn-n', text: String(n) }))));
    paintFilter();
  } else if (providers.length > 4) {
    const select = el('select', { class: 'as-select', 'aria-label': 'Show one provider' }, el('option', { value: '', text: `All providers (${entries.length})` }),
      [...providers].sort((x, y) => provName.get(x).localeCompare(provName.get(y))).map((p) => el('option', { value: p, text: `${provName.get(p)} (${count(p)})` })));
    select.addEventListener('change', () => { filter = select.value; paint(); });
    filterRow = el('div', { class: 'as-filter' }, el('span', { class: 'as-filter-l', text: 'Provider' }), select);
  }

  const asText = () => [b.title, '', ['Date', 'Date is', 'Provider', 'What happened', 'Amount', 'Source'].join('\t'),
    ...shown().map((e) => [e.date || 'undated', e.dateKind, e.provider, e.text, e.amount != null ? fmtMoney(e.amount) : '', e.cites.map(citeName).join('; ')].join('\t'))].join('\n');

  const lit = () => nodes(shown().flatMap((e) => e.cites));
  const graphBtn = el('button', { type: 'button', class: 'btn quiet sm', title: 'Light every source of this document in the graph', hidden: !lit().length, onclick: () => ctx.showInGraph(lit()) }, graphIcon(), 'Show in graph');
  const wide = el('button', { type: 'button', class: 'btn quiet sm as-wide-btn', text: ctx.isWide() ? 'Narrow' : 'Expand', onclick: () => ctx.toggleWide() });
  const copy = el('button', { type: 'button', class: 'btn quiet sm', text: 'Copy' });
  copy.addEventListener('click', async () => ctx.toast(await copyText(asText()) ? 'Copied as text, one entry per line.' : 'Could not copy.'));
  const save = el('button', { type: 'button', class: 'btn sm', text: b.saved ? 'Saved' : 'Save to documents', disabled: !!b.saved });
  save.addEventListener('click', async () => {
    save.disabled = true; save.textContent = 'Saving…';
    try { await ctx.save(b); save.textContent = 'Saved'; ctx.toast('Saved. It is listed under Documents in this panel.'); }
    catch (err) { save.disabled = false; save.textContent = 'Save to documents'; ctx.toast(`Not saved: ${err.message}`, 'error'); }
  });

  paint();
  return el('section', { class: 'as-doc' },
    el('header', { class: 'as-doc-h' },
      el('div', { class: 'as-doc-t' }, el('span', { class: 'as-doc-eyebrow', text: 'Document' }), el('h3', { text: b.title }), meta),
      el('div', { class: 'as-doc-act' }, graphBtn, wide, copy, save)),
    filterRow, years, body);
}

export function renderBlock(b, ctx) {
  if (b.type === 'heading') return el('h3', { class: 'as-h3', text: b.text });
  if (b.type === 'table') return tableBlock(b, ctx);
  if (b.type === 'document') return documentBlock(b, ctx);
  return el('p', { class: 'as-p' }, b.sentences.map((s) => sentence(s, ctx)));
}

// ---------------------------------------------------------------- "What it read"

function stepItem(s) {
  const tok = s.tokensIn != null || s.tokensOut != null ? `${fmtTok(s.tokensIn ?? 0)} in, ${fmtTok(s.tokensOut ?? 0)} out` : null;
  const args = Object.keys(s.args || {}).length ? JSON.stringify(s.args, null, 1) : null;
  const model = s.tool === 'model';
  return el('li', { class: `as-step${s.error ? ' err' : ''}${model ? ' model' : ''}` }, el('details', {},
    el('summary', {}, el('span', { class: 'as-step-w', text: s.words }), el('span', { class: 'as-step-m', text: dot(s.cached ? 'kept from earlier' : fmtMs(s.ms), fmtUsd(s.usd)) })),
    el('dl', { class: 'as-step-d' },
      el('dt', { text: model ? 'Step' : 'Tool' }), el('dd', {}, model ? 'the model itself' : el('code', { text: s.tool })),
      args ? [el('dt', { text: 'Asked with' }), el('dd', {}, el('pre', { text: args }))] : null,
      s.note ? [el('dt', { text: model ? 'Its plan' : 'Result' }), el('dd', { text: s.note })] : null,
      s.items != null ? [el('dt', { text: 'Returned' }), el('dd', { text: plural(s.items, 'item') })] : null,
      tok ? [el('dt', { text: 'Tokens' }), el('dd', { text: tok })] : null,
      s.error ? [el('dt', { text: 'Error' }), el('dd', { class: 'error', text: s.error })] : null)));
}

// The whole trace as a list, grouped by round: each round is headed by what the model planned, with
// the calls it made under it.
export function traceBody(trace, cost) {
  const rounds = new Map();
  for (const s of trace) {
    const k = s.round ?? 0;
    if (!rounds.has(k)) rounds.set(k, { model: null, tools: [] });
    const r = rounds.get(k);
    if (s.tool === 'model' && !r.model) r.model = s; else r.tools.push(s);
  }
  const list = el('ol', { class: 'as-steps' }, [...rounds].sort((x, y) => x[0] - y[0]).map(([k, r]) => {
    const m = r.model;
    const tok = m && (m.tokensIn != null || m.tokensOut != null) ? `${fmtTok(m.tokensIn ?? 0)} in, ${fmtTok(m.tokensOut ?? 0)} out` : null;
    return el('li', { class: 'as-round' },
      el('div', { class: 'as-round-h' }, el('span', { class: 'as-round-t', text: m ? (m.note || m.words) : (k === 0 ? 'Read before answering' : `Round ${k}`) }),
        m ? el('span', { class: 'as-step-m', text: dot(m.cached ? 'kept from earlier' : fmtMs(m.ms), tok, fmtUsd(m.usd)) }) : null),
      r.tools.length ? el('ol', { class: 'as-round-l' }, r.tools.map(stepItem)) : null);
  }));
  if (!trace.length) list.append(el('li', { class: 'as-step', text: 'No tool was called: this answer used only what was sent.' }));
  const tok = cost.tokensIn != null || cost.tokensOut != null ? `${fmtTok((cost.tokensIn ?? 0) + (cost.tokensOut ?? 0))} tokens` : null;
  list.append(el('li', { class: 'as-step sent', text: dot(cost.model ? `Model: ${cost.model}` : null, tok, plural(trace.filter((s) => s.tool !== 'model').length, 'call')) }));
  return list;
}

function traceView() {
  const meta = el('span', { class: 'as-trace-m' });
  const list = el('ol', { class: 'as-steps' });
  const live = el('li', { class: 'as-step live' }, el('span', { class: 'as-spin', 'aria-hidden': 'true' }), el('span', { class: 'as-step-w', text: 'working…' }));
  const sent = el('li', { class: 'as-step sent' });
  list.append(sent, live);
  const node = el('details', { class: 'as-trace', open: true }, el('summary', {}, el('span', { class: 'as-trace-t', text: 'What it read' }), meta), list);
  let n = 0;
  return {
    node,
    sent(text) { sent.textContent = text; },
    add(step) { n += 1; live.before(stepItem(step)); meta.textContent = plural(n, 'step'); },
    // The final trace replaces the live one, grouped by round: each round is headed by what the model
    // planned, with the calls it made under it. By now record ids resolve to their titles.
    finish(trace, cost) {
      const rounds = new Map();
      for (const s of trace) {
        const k = s.round ?? 0;
        if (!rounds.has(k)) rounds.set(k, { model: null, tools: [] });
        const r = rounds.get(k);
        if (s.tool === 'model' && !r.model) r.model = s; else r.tools.push(s);
      }
      list.replaceChildren(sent, ...[...rounds].sort((x, y) => x[0] - y[0]).map(([k, r]) => {
        const m = r.model;
        const tok = m && (m.tokensIn != null || m.tokensOut != null) ? `${fmtTok(m.tokensIn ?? 0)} in, ${fmtTok(m.tokensOut ?? 0)} out` : null;
        return el('li', { class: 'as-round' },
          el('div', { class: 'as-round-h' }, el('span', { class: 'as-round-t', text: m ? (m.note || m.words) : (k === 0 ? 'Read before answering' : `Round ${k}`) }),
            m ? el('span', { class: 'as-step-m', text: dot(m.cached ? 'kept from earlier' : fmtMs(m.ms), tok, fmtUsd(m.usd)) }) : null),
          r.tools.length ? el('ol', { class: 'as-round-l' }, r.tools.map(stepItem)) : null);
      }));
      const calls = trace.filter((s) => s.tool !== 'model').length;
      const tok = cost.tokensIn != null || cost.tokensOut != null ? `${fmtTok((cost.tokensIn ?? 0) + (cost.tokensOut ?? 0))} tokens` : null;
      meta.textContent = dot(plural(calls, 'call'), fmtMs(cost.ms), tok, fmtUsd(cost.usd));
      if (!trace.length) list.append(el('li', { class: 'as-step', text: 'No tool was called: this answer used only what was sent.' }));
      if (cost.model) list.append(el('li', { class: 'as-step sent', text: `Model: ${cost.model}` }));
      node.open = false;
    },
  };
}

// ---------------------------------------------------------------- one answer

// The view of one answer while it is being made and after: status, the blocks, the trace under them.
export function answerView(ctx, { modeLabel, sentLine }) {
  const elapsed = el('span', { class: 'as-elapsed' });
  const statusText = el('span', { text: 'Reading the file' });
  const status = el('p', { class: 'as-status', role: 'status' }, el('span', { class: 'as-spin', 'aria-hidden': 'true' }), statusText, elapsed);
  const body = el('div', { class: 'as-blocks' });
  const trace = traceView();
  trace.sent(sentLine);
  const foot = el('div', { class: 'as-meta' });
  const node = el('article', { class: 'as-answer', 'aria-busy': 'true' }, status, body, trace.node, foot);
  const t0 = performance.now();
  const timer = setInterval(() => { elapsed.textContent = `${Math.round((performance.now() - t0) / 1000)} s`; }, 1000);
  const stop = () => { clearInterval(timer); node.removeAttribute('aria-busy'); };

  return {
    node,
    event(ev) {
      if (ev.type === 'step') trace.add(ev.step);
      else if (ev.type === 'status' && ev.text) statusText.textContent = ev.text;
    },
    finish(answer) {
      stop();
      status.remove();
      body.replaceChildren(...answer.blocks.map((b) => renderBlock(b, ctx)));
      if (!answer.blocks.length) body.append(el('p', { class: 'as-p muted', text: 'Nothing came back for this request.' }));
      answer.notes.forEach((n) => body.append(el('p', { class: 'as-note', text: n })));
      const cost = { ...answer.cost, ms: answer.cost.ms ?? Math.round(performance.now() - t0) };
      trace.finish(answer.trace, cost);
      const lit = nodes(answer.blocks.flatMap(blockCites));
      foot.append(el('span', { class: 'as-meta-t', text: dot(modeLabel, fmtMs(cost.ms), fmtUsd(cost.usd)) }));
      if (lit.length) foot.append(el('button', { type: 'button', class: 'as-link', onclick: () => ctx.showInGraph(lit) }, graphIcon(), `Show ${plural(lit.length, 'source')} in graph`));
      return cost;
    },
    fail(err, retry) {
      stop();
      trace.node.remove();
      const stopped = err?.name === 'AbortError';
      status.className = stopped ? 'as-status muted' : 'as-status error';
      status.replaceChildren(stopped ? 'Stopped. ' : `The assistant could not answer: ${err?.message || 'no reply'} `);
      if (retry) status.append(el('button', { type: 'button', class: 'as-link', text: 'Try again', onclick: retry }));
    },
  };
}
