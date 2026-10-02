// Settings. The only screen that names the system the matter is imported from; the name comes from the server.
import { el, fmtDateTime, toast } from '../../js/util.js';
import { loadCss } from './_css.js';
import { themePicker } from '../js/theme.js';

const KIND = {
  communication: 'Communications', note: 'Notes', document: 'Documents', task: 'Tasks', calendar_entry: 'Calendar entries',
  contact: 'Contacts', custom_field: 'Fields', expense: 'Expenses', relationship: 'Relationships', folder: 'Folders', matter: 'Matter',
};
const money = (n) => (n == null ? '-' : `$${Number(n).toFixed(2)}`);
const dur = (s) => (s == null ? null : s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

export default async function mount(host, c, ctx) {
  loadCss('share.css');
  // Settings is firm level, so it may be opened with no case in hand: fall back to the case the server last imported.
  let status = null;
  try { status = await ctx.api('/api/settings/import'); } catch { /* an older server: the section says so */ }
  const matterId = ctx.matterId ?? status?.selected_matter_id ?? null;
  if (matterId != null && ctx.matterId == null) ctx = { ...ctx, matterId };
  c = c || ctx.caseModel || (matterId != null ? await ctx.api(`/api/matters/${matterId}/case`).catch(() => null) : null) || {};
  const m = c.meta || {};
  const d = m.digest || {};
  const src = status?.source || 'the source system';

  // ---- connection and last sync
  const result = el('div', { class: 'st-result', role: 'status', 'aria-live': 'polite' });
  const lastLine = el('p', { class: 'small' });
  const paintLast = (at) => { lastLine.textContent = at ? `Last synced ${fmtDateTime(at)}.` : 'This matter has not been synced yet.'; };
  paintLast(m.synced_at);
  const noCase = !status?.import_href && matterId == null;
  const sync = el('button', { class: 'btn primary', type: 'button', disabled: noCase }, 'Sync now');
  if (noCase) result.textContent = 'Open a case to sync it.';
  sync.addEventListener('click', async () => {
    sync.disabled = true; result.textContent = `Reading ${src}...`;
    try {
      const r = await ctx.api(status?.import_href || `/api/matters/${ctx.matterId}/sync`, { method: 'POST' });
      const n = r.changed;
      result.replaceChildren(
        el('strong', { text: `${n} changed record${n === 1 ? '' : 's'}. ` }),
        `Records that did not change were left as they are and not analysed again. ${r.documents_downloaded ? `${r.documents_downloaded} document file${r.documents_downloaded === 1 ? '' : 's'} fetched. ` : ''}${r.requests} read${r.requests === 1 ? '' : 's'} made, none of them writes.`,
        ...(r.warnings || []).map((w) => el('div', { class: 'small', text: w })));
      paintLast(new Date().toISOString());
    } catch (err) { result.textContent = `Sync failed: ${err.message}`; }
    sync.disabled = false;
  });

  const connected = status ? status.connected : null;
  const connection = el('section', { class: 'card st-card' },
    el('h2', { text: 'Import' }),
    el('dl', { class: 'st-dl' },
      el('dt', { text: 'Source' }), el('dd', { text: status?.source || 'unknown' }),
      el('dt', { text: 'Connection' }), el('dd', { text: connected == null ? 'unknown' : connected ? 'Connected' : status.configured ? 'Not connected' : 'Not configured' }),
      el('dt', { text: 'Access' }), el('dd', { text: 'Read-only. The app only reads; it never creates, changes or deletes anything there.' })),
    lastLine,
    el('div', { class: 'row' }, sync, connected === false && status?.configured ? el('a', { class: 'btn', href: status.connect_href || '/oauth/start' }, `Connect to ${src}`) : null),
    result);

  // ---- what was imported
  const counts = Object.entries(m.clio_counts || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const imported = el('section', { class: 'card st-card' },
    el('h2', { text: 'Imported records' }),
    counts.length ? el('dl', { class: 'st-dl' }, counts.flatMap(([k, n]) => [el('dt', { text: KIND[k] || k.replace(/_/g, ' ') }), el('dd', { class: 'num', text: String(n) })]))
      : el('p', { class: 'muted small', text: 'Nothing imported yet.' }));

  // ---- fee
  const fee = await feeSection(ctx);

  // ---- digest cost and time
  const usage = d.usage || [];
  const tokens = usage.reduce((a, u) => ({ i: a.i + (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0), o: a.o + (u.output_tokens || 0) }), { i: 0, o: 0 });
  const rows = [
    ['State', d.state === 'complete' ? 'Complete' : String(d.state || 'not started').replace(/_/g, ' ')],
    ['Analysed', d.pages_total ? `${d.items_digested} of ${d.items_total} records, ${d.pages_digested} of ${d.pages_total} pages` : `${d.items_digested ?? 0} of ${d.items_total ?? 0} records`],
    ['Last run', d.last_run_at ? fmtDateTime(d.last_run_at) : 'never'],
    ['Cost, whole case so far', money(d.cost_usd_total)],
    ['Cost, last incremental run', d.last_run_at ? money(d.cost_usd_last_run) : '-'],
    ['Cost of live checks', d.check_calls ? `${money(d.check_cost_usd)} over ${d.check_calls} calls` : '-'],
    ['Last card rebuild', d.reconcile_estimate_seconds != null ? `${dur(d.reconcile_estimate_seconds)}, ${money(d.reconcile_estimate_usd)}` : '-'],
    ['Tokens', tokens.i || tokens.o ? `${tokens.i.toLocaleString()} in, ${tokens.o.toLocaleString()} out` : '-'],
    ['Model', usage.map((u) => u.model).join(', ') || '-'],
  ];
  const digest = el('section', { class: 'card st-card' },
    el('h2', { text: 'Analysis cost and time' }),
    el('dl', { class: 'st-dl' }, rows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })])),
    el('p', { class: 'small muted', text: 'Costs are summed from the token counts the model provider reports. Total elapsed time is not recorded; the only duration held is the last card rebuild.' }));

  const spend = await spendSection(ctx);
  host.replaceChildren(el('div', { class: 'v2-page-h' }, el('h1', { text: 'Settings' })), el('div', { class: 'st-grid' }, el('section', { class: 'card st-card' }, el('h2', { text: 'Appearance' }), themePicker()), connection, imported, fee, digest), spend, integrations());
  return null;
}

async function feeSection(ctx) {
  let f = null;
  try { f = await ctx.api('/api/settings/fee'); } catch { /* shown below */ }
  const pct = el('input', { type: 'number', min: 0, max: 100, step: 0.1, value: f?.percent ?? '', 'aria-label': 'Fee percent', placeholder: 'not set' });
  const basis = el('select', { 'aria-label': 'Fee basis' }, el('option', { value: 'gross', text: 'of gross recovery' }), el('option', { value: 'after_costs', text: 'of recovery after costs' }));
  basis.value = f?.basis || 'gross';
  const line = el('span', { class: 'small muted', role: 'status', text: f?.percent == null ? 'Not set: the fee and net-to-client figures stay unknown.' : '' });
  const save = el('button', { class: 'btn', type: 'button', disabled: !f }, 'Save');
  save.addEventListener('click', async () => {
    const v = pct.value === '' ? null : Number(pct.value);
    try {
      await ctx.api('/api/settings/fee', { method: 'PUT', body: { percent: v, basis: basis.value } });
      line.textContent = v == null ? 'Fee cleared.' : 'Saved.';
      toast('Fee saved.');
    } catch (err) { line.textContent = `Not saved: ${err.message}`; }
  });
  return el('section', { class: 'card st-card' },
    el('h2', { text: 'Firm fee' }),
    el('p', { class: 'small muted', text: 'The contingency fee is the firm\'s own term. It is kept in this app, not in the source system.' }),
    el('div', { class: 'row' }, el('label', { class: 'small' }, 'Fee ', pct, ' %'), basis, save), line);
}

// No connector to other legal software exists, so there are no tiles: nothing here may look connectable.
function integrations() {
  return el('section', { class: 'v2-card st-int' },
    el('h2', { text: 'Integrations' }),
    el('p', { class: 'muted', text: 'Connections to other legal software are not built; the import today is from the practice-management system and from a zip of the file.' }));
}

const GROUP_WORD = { digest: 'Case analysis', check: 'Live checks', card_design: 'Card design', dashboard_design: 'Dashboard design', assistant: 'Assistant', ingestion: 'Ingestion', negotiation: 'Negotiation', other: 'Other', total: 'Total' };
const usd = (n) => (n == null ? '-' : `$${Number(n).toFixed(2)}`);
const num = (n) => (n == null ? '-' : Number(n).toLocaleString());

// Spend by purpose, from GET /api/spend. Every figure is the server's; nothing is computed here except the sign wording.
async function spendSection(ctx) {
  let d = null;
  try { d = await ctx.api(ctx.matterId != null ? `/api/spend?matter_id=${ctx.matterId}` : '/api/spend'); } catch { /* an older server has no such route */ }
  const card = el('section', { class: 'card st-card st-spend' }, el('h2', { text: 'Spend by purpose' }));
  if (!d) { card.append(el('p', { class: 'muted small', text: 'Spend is not available from this server.' })); return card; }
  const effect = (l) => (l.caching_effect_usd == null ? '-' : l.caching_effect_usd > 0 ? `saved ${usd(l.caching_effect_usd)}` : l.caching_effect_usd < 0 ? `cost ${usd(-l.caching_effect_usd)} more` : 'none');
  const row = (l, strong) => el('tr', { class: strong ? 'st-total' : null },
    el('td', { text: GROUP_WORD[l.group] || l.group }), el('td', { class: 'num', text: num(l.calls) + (l.failed ? ` (${l.failed} failed)` : '') }),
    el('td', { class: 'num', text: num(l.input_tokens) }), el('td', { class: 'num', text: l.cached_share == null ? '-' : `${Math.round(l.cached_share * 100)}%` }),
    el('td', { class: 'num', text: num(l.output_tokens) }), el('td', { class: 'num', text: usd(l.usd) }), el('td', { class: 'num', text: effect(l) }));
  const st = d.stored || {};
  card.append(
    el('div', { class: 'table-wrap' }, el('table', { class: 'v2-table' },
      el('thead', {}, el('tr', {}, ['Purpose', 'Calls', 'Input tokens', 'From cache', 'Output tokens', 'Cost', 'Caching'].map((h, i) => el('th', { class: i ? 'num' : '', scope: 'col', text: h })))),
      el('tbody', {}, (d.lines || []).map((l) => row(l, false)), d.total ? row(d.total, true) : null))),
    el('p', { class: 'small muted', text: `Kept so it is never paid for twice: ${num(st.pages_read)} pages read${st.pages_read_here != null ? ` (${num(st.pages_read_here)} paid for here)` : ''}, ${num(st.records_read)} records read, ${num(st.check_answers)} check answers, ${num(st.incoming_checks)} incoming checks.${d.prices_read_on ? ` Prices read on ${d.prices_read_on}.` : ''}` }),
    ...(d.notes || []).map((n) => el('p', { class: 'small muted', text: n })));
  return card;
}
