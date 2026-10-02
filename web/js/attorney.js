// Attorney view: matter picker, case dashboard, and the share screen.
import { api, showMockBanner, isSigningIn } from './api.js';
import { el, empty, fmtDate, fmtDateTime, dayWords, pill, statusPill, section, toast, initials } from './util.js';
import { chip, chips, openSources } from './drawer.js';
import { renderRiver } from './river.js';
import { renderShare, updateShare, setUnreviewed } from './share.js';
import { renderWrite } from './write.js';
import { movesCard } from './moves.js';
import { riverDetail } from './riverdetail.js';

showMockBanner();

const view = document.getElementById('view');
const picker = document.getElementById('matter');
const digestBtn = document.getElementById('digest');
const digestPanel = document.getElementById('digest-panel');
let matterId = null;
let current = null;           // last CaseModel
let tab = 'case';

// ---------------------------------------------------------------- boot

function lastMatter(set) {
  try {
    if (set) localStorage.setItem('lastMatter', JSON.stringify(set));
    else return JSON.parse(localStorage.getItem('lastMatter') || 'null');
  } catch { /* storage blocked */ }
  return null;
}

let offline = false;

async function boot() {
  let list;
  try { list = await api('/api/matters'); } catch (err) {
    // Clio (or the list call) is unreachable: fall back to the matter opened last, from our own copy.
    const last = lastMatter();
    if (!last) return fail('Could not reach the server', err, boot);
    list = { connected: false, items: [{ id: Number(last.id), display_number: last.label }], selected_matter_id: Number(last.id), connect_href: '/oauth/start' };
  }
  if (!list.connected && !(list.items || []).length) {
    view.replaceChildren(el('div', { class: 'card connect' },
      el('h1', { text: 'Connect the firm\'s Clio account' }),
      el('p', { class: 'muted', text: 'The app reads the matter from Clio (read-only) and keeps its own copy. Clio is never written to.' }),
      el('a', { class: 'btn primary', href: list.connect_href || '/oauth/start' }, 'Connect Clio')));
    return;
  }
  offline = !list.connected;
  picker.replaceChildren(...list.items.map((m) => el('option', { value: String(m.id), text: [m.display_number, m.client_name, m.description].filter(Boolean).join('  |  ') || `Matter ${m.id}` })));
  const fromHash = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('m'));
  matterId = [fromHash, list.selected_matter_id, list.items[0]?.id].find((id) => id && list.items.some((m) => m.id === id)) ?? null;
  if (matterId == null) { view.replaceChildren(empty('Clio is connected, but no matters were found.')); return; }
  const chosen = list.items.find((m) => m.id === matterId);
  if (!offline) lastMatter({ id: matterId, label: chosen.display_number || chosen.description || `Matter ${matterId}` });
  picker.value = String(matterId);
  picker.addEventListener('change', () => { matterId = Number(picker.value); sessionStorageSafe('remove'); loadCase(); });
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tickNow(); });
  document.getElementById('sync').addEventListener('click', resync);
  digestBtn.addEventListener('click', () => { const open = digestPanel.hidden; digestPanel.hidden = !open; digestBtn.setAttribute('aria-expanded', String(open)); });
  await loadCase();
  tick();
}

function paintOffline() {
  let b = document.getElementById('offline');
  if (!offline) { b?.remove(); return; }
  if (!b) { b = el('div', { id: 'offline', class: 'offline-banner', role: 'status' }); document.querySelector('.top').after(b); }
  b.replaceChildren(`Clio not reachable: showing the last sync from ${fmtDateTime(current?.meta?.synced_at) || 'an earlier time'}. `, el('a', { href: '/oauth/start', text: 'Reconnect Clio' }));
}

let retry = null;
function fail(title, err, again) {
  clearTimeout(retry);
  view.replaceChildren(el('div', { class: 'card' }, el('h1', { text: title }),
    el('p', { class: 'error', text: `${err.status ? `HTTP ${err.status}: ` : ''}${err.message}` }),
    el('p', { class: 'muted', text: again ? 'Trying again every few seconds. The matter may still be syncing.' : 'Check that the server is running and Clio is connected, then reload.' }),
    again ? el('button', { class: 'btn', type: 'button', onclick: again }, 'Try now') : null));
  if (again) retry = setTimeout(again, 4000);
}

function route() {
  document.querySelectorAll('dialog[open]').forEach((d) => d.close());
  tab = location.hash.startsWith('#share') ? 'share' : location.hash.startsWith('#write') ? 'write' : 'case';
  for (const t of ['case', 'write', 'share']) document.getElementById(`tab-${t}`)?.classList.toggle('on', tab === t);
  if (current) draw();
}

// "Since you last opened" is measured from the previous open. Reloading the tab
// must not reset it, so the cut-off is remembered for this browser tab.
function sessionStorageSafe(op, v) {
  const key = `since:${matterId}`;
  try {
    if (op === 'get') return sessionStorage.getItem(key);
    if (op === 'set') sessionStorage.setItem(key, v);
    if (op === 'remove') sessionStorage.removeItem(key);
  } catch { /* private mode */ }
  return null;
}

async function loadCase() {
  view.replaceChildren(el('p', { class: 'muted pad', text: 'Reading the matter...' }));
  const remembered = sessionStorageSafe('get');
  try {
    const q = remembered && remembered !== 'none' ? `?since=${encodeURIComponent(remembered)}` : '';
    current = await api(`/api/matters/${matterId}/case${q}`);
  } catch (err) { return fail('Could not load this matter', err, loadCase); }
  if (remembered == null) {
    try {
      const o = await api(`/api/matters/${matterId}/opened`, { method: 'POST' });
      sessionStorageSafe('set', o.previous || 'none');
    } catch { /* non-fatal: only the "since" marker is lost */ }
  }
  paintOffline();
  route();
}

async function startDigest() {
  try { await api(`/api/matters/${matterId}/digest`, { method: 'POST' }); toast('Digest started. Sections fill in as it runs.'); tickNow(); }
  catch (err) { toast(`Digest not started: ${err.message}`, 'error'); }
}

// Tokens spent because of this sync: none when nothing changed; otherwise the digest's figure once it has run on the changes.
function tokenText(r, d, startedAt) {
  if (r.changed === 0) return ', 0 tokens';
  if (d?.last_run_at && d.last_run_at > startedAt && d.input_tokens_last_run != null) return `, ${(d.input_tokens_last_run + (d.output_tokens_last_run || 0)).toLocaleString('en-US')} tokens`;
  return ', digest of the changed items not run yet';
}

let syncing = false;
async function resync() {
  syncing = true;
  const startedAt = new Date().toISOString();
  const btn = document.getElementById('sync');
  btn.disabled = true; btn.textContent = 'Reading Clio...'; document.getElementById('syncline').textContent = '';
  try {
    const r = await api(`/api/matters/${matterId}/sync`, { method: 'POST' });
    const before = current?.meta?.digest;
    await loadCase();
    const d = current?.meta?.digest;
    const line = `${r.changed} changed item${r.changed === 1 ? '' : 's'}, ${r.requests} Clio read${r.requests === 1 ? '' : 's'} (GET only), last digest run $${(d?.cost_usd_last_run ?? 0).toFixed(2)}${tokenText(r, d, startedAt)}${before && d && d.items_stale ? `, ${d.items_stale} awaiting digest` : ''}`;
    document.getElementById('syncline').textContent = `Synced ${fmtDateTime(new Date().toISOString())}: ${line}`;
    toast(`${line}.`);
    if (r.warnings?.length) toast(r.warnings.join(' '), 'error');
  } catch (err) { toast(`Sync failed: ${err.message}`, 'error'); }
  btn.disabled = false; btn.textContent = 'Re-sync from Clio';
  syncing = false;
}

// ---------------------------------------------------------------- drawing

let writer = null;
let slots = {};
let sigs = {};

function sigOf(c) {
  const { summary, ...brief } = c.brief;
  return {
    header: [brief, c.matter], river: [c.river, c.nodes], moves: c.moves, conflicts: [c.conflicts, c.claims, c.meta?.digest?.reconcile_stale, c.meta?.digest?.changed_since_reconcile], summary: summary, fields: c.custom_fields, incoming: c.incoming,
    agenda: c.agenda, changes: c.changes, timeline: c.timeline,
  };
}
const stringify = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, JSON.stringify(v)]));

function draw() {
  try { paintDigest(current.meta.digest); } catch (err) { console.error('digest badge failed', err); }
  if (tab === 'write') {
    view.replaceChildren();
    writer = renderWrite(view, current, { matterId });
  } else if (tab === 'share') {
    view.replaceChildren();
    renderShare(view, current, { matterId, refreshBadges, reload: async () => { current = await reloadQuiet(); return current; } });
  } else {
    slots = buildSlots(current);
    sigs = stringify(sigOf(current));
    view.replaceChildren(...arrange(slots));
  }
}

// ---------------------------------------------------------------- live refresh

const hasFocus = (node) => node && node.contains(document.activeElement);

function announce(prev, next) {
  const name = new Map(next.contacts.map((x) => [x.id, x.name]));
  const oldAsks = new Map((prev.providers || []).flatMap((p) => p.asks.map((a) => [a.id, a])));
  const oldShares = new Map((prev.providers || []).flatMap((p) => p.shares.map((s) => [s.id, s])));
  for (const p of next.providers) {
    const oldP = (prev.providers || []).find((x) => x.contact.id === p.contact.id);
    const seenReq = new Set((oldP?.requests || []).map((r) => r.id));
    for (const r of p.requests || []) if (!seenReq.has(r.id) && r.state === 'open') toast(`${p.contact.name} asked: ${r.question || r.kind}`, 'live');
    for (const a of p.asks) if (a.reply && !oldAsks.get(a.id)?.reply) toast(`${p.contact.name} replied to a request.`, 'live');
    for (const s of p.shares) {
      const o = oldShares.get(s.id);
      if (o && !o.first_opened_at && s.first_opened_at) toast(`${name.get(p.contact.id) || p.contact.name} opened the link.`, 'live');
      else if (o && s.open_count > o.open_count) toast(`${p.contact.name} opened the link again (${s.open_count} opens).`, 'live');
    }
  }
  const key = (i) => `${i.source?.kind}:${i.source?.clio_id}:${i.at}:${i.change}`;
  const seen = new Set(prev.changes.items.map(key));
  const fresh = next.changes.items.filter((i) => !seen.has(key(i)));
  if (fresh.length) toast(`${fresh.length} new item${fresh.length === 1 ? '' : 's'} since you opened this matter.`, 'live');
}

// Badge: providers whose texts the file contradicts and nobody has reviewed. Read from stored results (never runs the checker).
async function refreshBadges() {
  try {
    const map = await api(`/api/matters/${matterId}/incoming-checks/unreviewed`);
    const total = Object.values(map || {}).reduce((a, b) => a + b, 0);
    const tabEl = document.getElementById('tab-share');
    if (tabEl) tabEl.replaceChildren('Share with providers', total ? pill(String(total), 'st-contested') : '');
    setUnreviewed(map);
  } catch { /* an older server has no such route: no badge */ }
}

function applyUpdate(next) {
  const prev = current;
  announce(prev, next);
  current = next;
  paintDigest(next.meta.digest);
  if (tab === 'write') return;                       // never touch the editor while someone is writing
  if (tab === 'share') { updateShare(next); return; }
  const now = stringify(sigOf(next));
  const fresh = buildSlots(next);
  for (const key of Object.keys(fresh)) {
    if (now[key] === sigs[key]) continue;
    if (hasFocus(slots[key])) continue;              // never replace what is being typed in; it catches up next tick
    slots[key].replaceWith(fresh[key]);
    slots[key] = fresh[key];
    sigs[key] = now[key];
  }
}

let polling = false;
let timer = null;
async function poll() {
  if (document.hidden || !current || polling || syncing || isSigningIn()) return;
  polling = true;
  try { applyUpdate(await reloadQuiet()); refreshBadges(); } catch { /* transient: the next tick retries */ }
  polling = false;
}
function tickNow() { clearTimeout(timer); tick(); }
async function tick() {
  await poll();
  const running = current?.meta?.digest?.state === 'running';
  timer = setTimeout(tick, running ? 3000 : 6000);
}

async function reloadQuiet() {
  const remembered = sessionStorageSafe('get');
  const q = remembered && remembered !== 'none' ? `?since=${encodeURIComponent(remembered)}` : '';
  return api(`/api/matters/${matterId}/case${q}`);
}

function paintProgress(d) {
  const p = document.getElementById('progress');
  const state = d.state || 'not_started';
  const live = state === 'running' || state === 'partial' || state === 'failed';
  p.hidden = !live;
  if (!live) return;
  const pages = d.pages_total > 0, done = pages ? d.pages_digested : d.items_digested, total = pages ? d.pages_total : d.items_total;
  const what = pages ? 'pages' : 'items';
  p.dataset.state = state;
  p.replaceChildren(
    el('span', { text: state === 'running' ? `Reading documents: ${done} of ${total} ${what}` : state === 'failed' ? 'Digest stopped with an error' : `Digest partial: ${done} of ${total} ${what}` }),
    total > 0 ? el('span', { class: 'bar' }, el('span', { class: 'fill', style: `width:${Math.min(100, Math.round((done / total) * 100))}%` })) : null);
}

function paintDigest(d) {
  paintProgress(d);
  const state = d.state || 'not_started';
  if (state === 'not_started' && !d.items_digested) {
    digestBtn.replaceChildren(el('span', { text: 'Not digested yet' }));
  } else {
    digestBtn.replaceChildren(
      el('span', { class: 'opt1', text: `${d.items_digested} of ${d.items_total} Clio items read` }),
      el('span', { class: 'sep opt1', text: '|' }),
      el('span', { class: 'opt2', text: `${Math.min(d.quotes_verified ?? 0, d.quotes_total ?? 0)} of ${d.quotes_total ?? 0} quotes found in source text${d.quotes_uncheckable ? `, ${d.quotes_uncheckable} quotes read from scans` : ''}` }),
      el('span', { class: 'sep opt2', text: '|' }),
      el('span', { text: `AI cost to digest: $${(d.cost_usd_total ?? 0).toFixed(2)}` }));
  }
  digestBtn.dataset.state = state;
  digestPanel.replaceChildren(
    el('dl', { class: 'meta' },
      el('dt', { text: 'State' }), el('dd', { text: state.replace('_', ' ') }),
      el('dt', { text: 'Last run' }), el('dd', { text: fmtDateTime(d.last_run_at) || 'never' }),
      el('dt', { text: 'Last run cost' }), el('dd', { text: `$${(d.cost_usd_last_run ?? 0).toFixed(2)}` }),
      el('dt', { text: 'Pages digested' }), el('dd', { text: `${d.pages_digested ?? 0} of ${d.pages_total ?? 0}` }),
      el('dt', { text: 'Awaiting re-digest' }), el('dd', { text: String(d.items_stale ?? 0) }),
      el('dt', { text: 'Quotes' }), el('dd', { text: `${Math.min(d.quotes_verified ?? 0, d.quotes_total ?? 0)} of ${d.quotes_total ?? 0} found in the source text${d.quotes_uncheckable ? `, ${d.quotes_uncheckable} read from scans (compare with the page image)` : ''}` })),
    el('button', { class: 'btn small', type: 'button', disabled: state === 'running', onclick: startDigest }, state === 'running' ? 'Digest running...' : 'Run digest on new or changed items'),
    d.usage?.length ? el('table', { class: 'usage' }, el('thead', {}, el('tr', {}, ['Model', 'Requests', 'Tokens in', 'Tokens out', 'USD'].map((h) => el('th', { text: h })))),
      el('tbody', {}, d.usage.map((u) => el('tr', {}, el('td', { text: u.model }), el('td', { text: String(u.requests) }), el('td', { text: String(u.input_tokens) }), el('td', { text: String(u.output_tokens) }), el('td', { text: u.cost_usd.toFixed(2) }))))) : null);
}

// One bad section must never blank the page: each is built on its own and a failure
// is shown in place, naming the section.
function guarded(name, build) {
  try { return build(); } catch (err) {
    console.error(`section "${name}" failed`, err);
    return el('section', { class: 'card section-error' }, el('h2', { text: name }),
      el('p', { class: 'error', text: `This section could not be drawn (${err.message}). The rest of the page is unaffected.` }));
  }
}

// Anything past its date is overdue, including items the firm is waiting on someone else for.
function pastDue(ag) {
  const seen = new Set();
  return [...(ag.overdue || []), ...(ag.waiting || []).filter((x) => x.days_from_today != null && x.days_from_today < 0)]
    .filter((x) => (seen.has(x.id) ? false : (seen.add(x.id), true)));
}

function fieldsCard(c) {
  const fs = (c.custom_fields || []).filter((f) => f.display && f.sources?.length);
  if (!fs.length) return el('div', { hidden: true });
  return section("From the firm's Clio fields", 'as entered in Clio, before any AI reading', el('div', { class: 'fields' }, fs.map((f) => el('button', {
    class: 'field', type: 'button', title: 'Open the full field', onclick: () => openSources(f.sources, f.label),
  }, el('span', { class: 'eyebrow', text: f.label }), el('span', { class: 'field-text', text: f.display })))));
}

// Outside communications already in Clio, read against the file. Firm only. Worded as "differs, check", not as a finding.
function incomingCard(c) {
  const flagged = (c.incoming || []).filter((x) => (x.counts?.contradicted || 0) + (x.counts?.out_of_date || 0) > 0);
  if (!flagged.length) return el('div', { hidden: true });
  const rows = flagged.map((x) => {
    const spans = (x.spans || []).filter((s) => s.verdict === 'contradicted' || s.verdict === 'out_of_date');
    return el('li', { class: 'incoming' },
      el('div', { class: 'ag-main' }, el('strong', { text: x.subject || 'Message' }), pill('differs from your file: check', 'st-assumed')),
      el('div', { class: 'small muted', text: [x.contact_name, x.contact_role, fmtDate(x.date)].filter(Boolean).join('  |  ') }),
      ...spans.map((s) => el('div', { class: 'annot' },
        el('div', { class: 'small' }, el('strong', { text: 'Their statement: ' }), s.text),
        s.message ? el('div', { class: 'small' }, el('strong', { text: 'Your file: ' }), s.message) : null,
        el('div', { class: 'chips' }, (s.sources || []).slice(0, 3).map((r) => chip(r))))),
      x.source ? chip(x.source) : null);
  });
  return section('Their statement vs your file', `${flagged.length} message${flagged.length === 1 ? '' : 's'} to check`, el('ul', { class: 'agenda' }, rows));
}

function buildSlots(c) {
  const claims = new Map((c.claims || []).map((x) => [x.id, x]));
  const ag = c.agenda || {};
  return {
    header: guarded('Header', () => header(c)),
    river: guarded('Value river', () => section('Value river', 'If the case resolved at the confirmed coverage figure', riverHost(c))),
    moves: guarded('What to do next', () => movesCard(c)),
    conflicts: guarded('Notes say, document shows', () => conflictsCard(c, claims)),
    fields: guarded('Clio fields', () => fieldsCard(c)),
    incoming: guarded('Their statement vs your file', () => incomingCard(c)),
    summary: guarded('Two-minute read', () => summaryCard(c)),
    agenda: guarded('Agenda', () => el('div', { class: 'grid-3' }, agendaCol('Overdue', pastDue(ag), 'Nothing overdue.'),
      agendaCol('Coming up', ag.coming || [], 'Nothing scheduled ahead.'), agendaCol('Waiting on someone else', ag.waiting || [], 'Nothing waiting on others.'))),
    changes: guarded('Since you last opened', () => changesCard(c)),
    timeline: guarded('What matters', () => timelineCard(c)),
  };
}

function arrange(s) {
  return [s.header, el('div', { class: 'hero' }, s.river, el('div', { class: 'hero-side' }, s.moves, s.conflicts)), s.fields, s.incoming, s.summary, s.agenda, el('div', { class: 'grid-2' }, s.changes, s.timeline)];
}

let firstRiverPaint = true;   // the river draws itself once, on the first paint only

function riverHost(c) {
  const host = el('div', { class: `river${firstRiverPaint ? ' draw' : ''}` });
  firstRiverPaint = false;
  renderRiver(host, c.river, (stage) => {
    // The gate opens the top-ranked disagreement that touches it: what the notes say beside the page that shows otherwise.
    if (stage.kind === 'gate') {
      const claims = new Map((c.claims || []).map((x) => [x.id, x]));
      const touching = [...(c.conflicts || [])].filter((x) => (stage.node_id && (x.node_ids || []).includes(stage.node_id)) || x.amount_at_stake != null)
        .sort((p, q) => (p.rank ?? 1e9) - (q.rank ?? 1e9));
      const top = touching[0];
      if (top) {
        const refs = [...top.document_claim_ids, ...top.notes_claim_ids].map((id) => claims.get(id)?.source).filter(Boolean);
        const card = document.querySelector(`[data-conflict="${CSS.escape(top.id)}"]`);
        if (card) { card.scrollIntoView({ block: 'center', behavior: 'smooth' }); card.classList.add('flash'); setTimeout(() => card.classList.remove('flash'), 1800); }
        if (refs.length) { openSources(refs, top.topic); return; }
      }
    }
    const refs = stage.sources?.length ? stage.sources : (c.nodes.find((n) => n.id === stage.node_id)?.sources || []);
    if (refs.length) openSources(refs, stage.label); else toast(`No source is recorded for "${stage.label}".`);
  });
  const wrap = el('div', {}, host, riverDetail(c), feeControl(c));
  return wrap;
}

// The contingency fee is the firm's own setting (not in Clio); until it is set the fee and net stages stay unknown.
function feeControl(c) {
  const pct = el('input', { type: 'number', min: 0, max: 100, step: 0.1, value: c.river.fee_percent ?? '', 'aria-label': 'Fee percent', placeholder: 'set %' });
  const basis = el('select', { 'aria-label': 'Fee basis' }, el('option', { value: 'gross', text: 'of gross' }), el('option', { value: 'after_costs', text: 'of net of costs' }));
  api('/api/settings/fee').then((f) => { if (f?.basis) basis.value = f.basis; }).catch(() => {});
  const save = el('button', { class: 'btn small', type: 'button' }, 'Save');
  const note = el('span', { class: 'small muted', role: 'status', text: c.river.fee_percent == null ? 'Fee not set: the fee and net stages stay unknown.' : '' });
  save.addEventListener('click', async () => {
    const v = pct.value === '' ? null : Number(pct.value);
    if (v != null && !(v >= 0 && v <= 100)) { note.textContent = 'Enter a percentage from 0 to 100.'; return; }
    save.disabled = true;
    try {
      await api('/api/settings/fee', { method: 'PUT', body: { percent: v, basis: basis.value } });
      await reloadQuiet(); draw();
    } catch (err) { note.textContent = `Not saved: ${err.message}`; save.disabled = false; }
  });
  return el('div', { class: 'fee-ctl' }, el('label', { class: 'small' }, 'Firm fee ', pct, ' %'), basis, save, note);
}

// ---- header brief

function metric(label, fact, { sub, big, plain } = {}) {
  if (!fact) {
    return el('div', { class: `metric none${big ? ' big' : ''}` }, el('div', { class: 'eyebrow', text: label }), el('div', { class: 'val muted', text: 'Not in the file' }));
  }
  const refs = fact.sources || [];
  const body = [el('div', { class: 'eyebrow', text: label }), el('div', { class: 'val', text: fact.display || '-' }),
    el('div', { class: 'sub' }, !plain && fact.status && fact.status !== 'unknown' ? statusPill(fact.status) : null, fact.conflict_ids?.length ? pill(`${fact.conflict_ids.length} difference${fact.conflict_ids.length === 1 ? '' : 's'} for review`, 'st-assumed') : null, plain && fact.detail ? el('span', { class: 'clamp2', title: fact.detail, text: fact.detail }) : null, sub || (fact.date ? el('span', { text: fmtDate(fact.date) }) : null)),
    big && refs[0]?.label ? el('div', { class: 'sub field-name', text: `Clio field: ${refs[0].label}` }) : null];
  if (!refs.length) return el('div', { class: `metric${big ? ' big' : ''}` }, body);
  return el('button', { class: `metric${big ? ' big' : ''}`, type: 'button', title: `Source: ${refs.map((r) => r.label).join(', ')}`, onclick: () => openSources(refs, label) }, body);
}

// The tile's status follows the river's coverage node so the two never disagree.
function coverageFact(fact, nodes) {
  const node = (nodes || []).find((n) => n.kind === 'coverage');
  return fact && node?.status && node.status !== fact.status ? { ...fact, status: node.status } : fact;
}

function header(c) {
  const b = c.brief, m = c.matter, client = m.client;
  const photo = b.client_photo;
  const avatar = photo
    ? el('button', { class: 'avatar', type: 'button', title: 'Open the photo source', onclick: () => openSources([photo.source], 'Client photo') },
      el('img', { src: photo.image_href, alt: `Photo of ${client?.name || 'client'}`, onerror: (e) => { e.target.replaceWith(el('span', { text: initials(client?.name) })); } }))
    : el('div', { class: 'avatar', title: 'No photo found in the file' }, el('span', { text: initials(client?.name) }));
  return el('div', { class: 'brief' },
    el('div', { class: 'who' }, avatar,
      el('div', {}, el('h1', { text: client?.name || m.description || 'Matter' }),
        el('p', { class: 'muted small', text: [m.display_number, m.practice_area, m.responsible_attorney, m.status].filter(Boolean).join('  |  ') }),
        b.alive ? el('p', { class: 'small', text: b.alive.display }) : null)),
    metric('Case value', b.case_value, { big: true }),
    metric('Coverage', coverageFact(b.coverage, c.nodes), { big: true }),
    metric('Stage', b.stage),
    metric('Firm spend', b.firm_spend),
    metric('Last client contact', b.last_client_contact),
    metric('Limitations date in Clio', b.limitations, { plain: true }));
}

function summaryCard(c) {
  const items = c.brief.summary || [];
  if (!items.length) return section('Two-minute read', null, empty('Not yet digested: the written summary appears after the first digest run.'));
  return section('Two-minute read', 'each line carries its source', el('ul', { class: 'summary' },
    items.map((f) => el('li', {}, el('span', { text: f.display }), chips(f.sources)))));
}

// ---- notes say / document shows

function staleBanner(c) {
  const d = c.meta?.digest;
  if (!d?.reconcile_stale) return null;
  const est = [d.reconcile_estimate_seconds != null ? `about ${Math.round(d.reconcile_estimate_seconds)} s` : null, d.reconcile_estimate_usd != null ? `$${d.reconcile_estimate_usd.toFixed(2)}` : null].filter(Boolean).join(', ');
  const btn = el('button', { class: 'btn small', type: 'button' }, 'Re-check');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try { await api(d.reconcile_href || `/api/matters/${matterId}/digest?reconcile=true`, { method: 'POST' }); toast('Re-checking the cards. They update as it runs.'); tickNow(); }
    catch (err) { toast(`Not started: ${err.message}`, 'error'); btn.disabled = false; }
  });
  return el('div', { class: 'callout warn small' },
    `${d.changed_since_reconcile ?? 'Some'} changed record${d.changed_since_reconcile === 1 ? '' : 's'} since the cards were built${d.reconciled_at ? ` (${fmtDateTime(d.reconciled_at)})` : ''}. `,
    est ? `Re-check takes ${est}. ` : '', btn);
}

function conflictsCard(c, claims) {
  const sorted = [...c.conflicts].sort((a, b) => b.severity - a.severity);
  const open = sorted.filter((x) => x.review !== 'dismissed');
  const host = section('Notes say, document shows', open.length ? `${open.length} for your review: a difference to look at, not an error` : null);
  if (!c.conflicts.length) {
    host.append(empty('No disagreement between the notes and the documents has been found. If the digest has not run, none can be.'));
    return host;
  }
  const ranked = [...c.conflicts].sort((p, q) => (p.rank ?? 1e9) - (q.rank ?? 1e9) || 0);   // the server's rank when present, else the order received
  const cards = ranked.map((x) => conflictCard(c, x, claims));
  const list = el('div', { class: 'conflicts' }, cards.slice(0, 3));
  const sb = staleBanner(c);
  if (sb) host.append(sb);
  host.append(list);
  if (cards.length > 3) {
    let open = false;
    const more = el('button', { class: 'linkish', type: 'button' }, `More (${cards.length - 3})`);
    more.addEventListener('click', () => { open = !open; list.replaceChildren(...(open ? cards : cards.slice(0, 3))); more.textContent = open ? 'Show fewer' : `More (${cards.length - 3})`; });
    host.append(more);
  }
  return host;
}

function conflictCard(c, x, claims) {
  const unverified = (cl, ids, cls) => {
    const n = cls === 'doc' ? x.document_quotes_verified : x.notes_quotes_verified;
    if (cls !== 'doc') return false;
    return n != null ? n < ids.length : cl[0]?.source?.quote_verified === false;
  };
  const side = (ids, label, cls) => {
    const cl = ids.map((id) => claims.get(id)).filter(Boolean);
    return el('button', { class: `side ${cls}`, type: 'button', disabled: !cl.length, onclick: () => openSources(cl.map((k) => k.source), x.topic) },
      el('span', { class: 'eyebrow', text: label }), el('span', { class: 'side-text', text: cl[0]?.text || 'No claim recorded' }),
      cl[0]?.date ? el('span', { class: 'muted small', text: `${fmtDate(cl[0].date)}${cl[0].date_source ? ` (${cl[0].date_source === 'document' ? 'document date' : 'Clio date'})` : ''}` }) : null);
  };
  const card = el('article', { class: `conflict sev-${x.severity} rv-${x.review}`, 'data-conflict': x.id });
  const review = el('div', { class: 'seg', role: 'group', 'aria-label': 'Your review' },
    ['unreviewed', 'confirmed', 'dismissed'].map((r) => el('button', {
      type: 'button', 'aria-pressed': String(x.review === r), text: r === 'unreviewed' ? 'Needs review' : r === 'confirmed' ? 'Confirmed' : 'Dismissed',
      onclick: async () => {
        const prev = x.review;
        setReview(card, x, r);
        try { await api(x.review_href || `/api/matters/${c.meta.matter_id}/conflicts/${encodeURIComponent(x.id)}/review`, { method: 'PUT', body: { review: r } }); }
        catch (err) { setReview(card, x, prev); toast(`Review not saved: ${err.message}`, 'error'); }
      },
    })));
  card.append(
    el('header', {}, el('h3', { text: x.topic }), x.kind_label ? pill(x.kind_label, '') : (x.kind === 'expert_opinion' || x.kind === 'opposing_view' ? pill("an expert's opinion in the file", '') : null),
    x.stale ? pill('record changed since', 'st-assumed') : null, x.amount_at_stake != null ? pill(`${new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(x.amount_at_stake)} behind the coverage gate`, 'amt') : null),
    el('p', { class: 'small', text: x.summary }),
    el('div', { class: 'sides' }, side(x.notes_claim_ids, x.kind === 'expert_opinion' || x.kind === 'opposing_view' ? 'Our file says' : 'Notes say', 'notes'), side(x.document_claim_ids, x.kind === 'answered_gap' ? 'This page is in the file' : (x.kind === 'expert_opinion' || x.kind === 'opposing_view' ? 'An expert says' : 'Document shows'), 'doc')),
    docQuoteLine(x),
    review);
  return card;
}

// Card-level and countable: only the quotes code could not find in the page text are counted.
function docQuoteLine(x) {
  const total = (x.document_claim_ids || []).length;
  const ok = x.document_quotes_verified;
  if (ok == null || !total || ok >= total) return null;
  return el('div', { class: 'small warn-text', text: `${total - ok} of ${total} document quote${total === 1 ? '' : 's'} not found in the page text: check the page` });
}

function setReview(card, x, r) {
  x.review = r;
  card.className = card.className.replace(/rv-\w+/, `rv-${r}`);
  card.querySelectorAll('.seg button').forEach((b, i) => b.setAttribute('aria-pressed', String(['unreviewed', 'confirmed', 'dismissed'][i] === r)));
}

// ---- agenda

function agendaCol(title, items, none) {
  const sub = items.length ? String(items.length) : null;
  const body = items.length ? el('ul', { class: 'agenda' }, items.map((a) => agendaRow(a))) : empty(none);
  return section(title, sub, body);
}

function agendaRow(a) {
  const when = a.days_from_today != null ? dayWords(a.days_from_today) : null;
  return el('li', { class: a.bucket === 'overdue' ? 'late' : '' },
    el('div', { class: 'ag-main' }, el('span', { class: 'ag-title', text: a.title }), a.is_limitations ? pill('limitations', 'st-contested') : null),
    el('div', { class: 'ag-meta small' },
      a.due ? el('button', { class: 'linkish', type: 'button', title: 'Open the source of this date', onclick: () => openSources([a.source], a.title), text: fmtDate(a.due) }) : el('span', { class: 'muted', text: 'no date' }),
      when ? el('span', { class: 'muted', text: when }) : null,
      a.waiting_on_name ? el('span', { text: `waiting on ${a.waiting_on_name}` }) : (a.assignee ? el('span', { class: 'muted', text: a.assignee }) : null)));
}

// ---- changes and timeline

function changesCard(c) {
  const ch = c.changes;
  const sub = ch.since ? `since ${fmtDateTime(ch.since)}` : 'first visit on record';
  const counts = Object.entries(ch.counts || {}).filter(([, n]) => n > 0);
  const head = counts.length ? el('p', { class: 'small muted', text: counts.map(([k, n]) => `${n} ${k.replace('_', ' ')}${n === 1 ? '' : 's'}`).join(', ') }) : null;
  const body = !ch.since ? empty('No earlier visit is recorded, so there is nothing to compare against. Re-open tomorrow and this lists what moved.')
    : ch.items.length ? el('ul', { class: 'changes' }, ch.items.map((i) => el('li', {},
      pill(i.change, `ck-${i.change}`), el('div', { class: 'ch-body' }, el('span', { text: i.title }), i.summary ? el('span', { class: 'muted small', text: i.summary }) : null),
      el('span', { class: 'muted small', text: fmtDateTime(i.at) }), chip(i.source))))
      : empty('Nothing changed since then.');
  return section('Since you last opened', sub, head, body);
}

function timelineCard(c) {
  const ranked = [...c.timeline].sort((a, b) => (b.importance - a.importance) || String(b.date).localeCompare(String(a.date))).slice(0, 10)
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));
  const body = ranked.length ? el('ul', { class: 'timeline' }, ranked.map((e) => el('li', {},
    e.sources?.length ? el('button', { class: 'linkish when', type: 'button', title: 'Open the source of this date', onclick: () => openSources(e.sources, e.label), text: fmtDate(e.date) }) : el('span', { class: 'when', text: fmtDate(e.date) }),
    el('span', { class: `src ${e.date_source === 'document' ? 'docd' : 'clio'}`, title: e.date_source === 'document' ? 'Date printed on the document' : 'Date recorded in Clio', text: e.date_source === 'document' ? 'date on document' : 'date in Clio' }),
    el('span', { class: 'what', text: e.label }), chips(e.sources))))
    : empty('Nothing on the calendar yet. The entries that matter are picked after the digest.');
  const digested = (c.meta.digest?.items_digested ?? 0) > 0;
  return section(digested ? 'What matters' : 'Calendar', ranked.length ? `${ranked.length} of ${c.timeline.length} entries` : null, body);
}

boot();
