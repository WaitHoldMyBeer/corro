// Your cases: every case at a glance, above the case view. A totals strip, the cases as a table (or cards),
// and the next dated items across all cases. Every figure is the server's (GET /api/firm/overview); nothing is
// added up here. Rows open the case; Archive and Restore only change what this product keeps, never the source.
import { el } from '../../js/util.js';
import { loadCss } from './_css.js';
import { listTab } from './_list.js';

loadCss('cases.css');

const OPEN_AGENDA = 10;
const MOCK = new URLSearchParams(location.search).get('mock');

// ---- the server's rows, read through one adapter so a renamed field changes one place

// The case rows of GET /api/firm/overview (contract: CaseOverview). A few older names are still read, so a
// server on the previous shape keeps working. Null means the case does not hold it: shown as unknown, never zero.
const fact = (f, amount, display) => (f || amount != null || display ? (f || { display, amount }) : null);
export function normCase(r, agenda = []) {
  const next = r.next_deadline || agenda.find((a) => a.case_id === r.id && a.date);
  return {
    id: r.id,
    name: r.name || '',
    number: r.display_number || r.number || '',
    client: r.client_name || r.client || '',
    source: r.source || '',
    sourceLabel: r.source_label || '',
    stage: r.stage || '',
    value: fact(r.value, r.estimated_value, r.estimated_value_display),
    coverage: fact(r.coverage, r.coverage_amount, r.coverage_display),
    limitations: r.limitations ? r.limitations : r.limitations_date ? { display: r.limitations_date, status: r.limitations_status } : null,
    next: next ? { date: next.date, title: next.title } : null,
    overdue: r.overdue,
    waiting: r.waiting,
    review: r.to_review ?? r.differences_to_review,
    last: r.last_activity,
    pages: r.pages,
    read: r.pages_read,
    readText: typeof r.reading === 'string' ? r.reading : '',
    readState: r.read_state,
    importedAt: r.imported_at || null,
    importing: !!r.import_running,
    archived: !!r.archived,
    error: r.error || null,
  };
}

const sourceWord = (r) => r.sourceLabel || (/upload/i.test(r.source) ? 'Uploaded' : r.source ? 'Imported' : '');
function reading(r) {
  if (r.importing) return 'Importing';
  if (r.readText) return r.readText;   // the server's words: "12 of 40 pages read", "not read yet", "no documents yet"
  if (r.readState === 'complete') return 'Read';
  if (r.readState === 'partial') return r.pages != null && r.read != null ? `${r.read} of ${r.pages} pages read` : 'Reading';
  if (r.readState === 'not_read') return 'not read yet';
  return '';
}

// ---- placeholder cases for ?mock=1 (words only; the number of cases follows ?cases=N)
function mockOverview() {
  const n = Math.max(0, Number(new URLSearchParams(location.search).get('cases') ?? 2));
  const f = (display, status) => ({ id: 'f', label: 'Placeholder', display, derivation: 'computed', status, sources: [], conflict_ids: [] });
  const cases = Array.from({ length: n }, (_, i) => ({
    id: 9000 + i, name: `Placeholder case ${i + 1}`, display_number: `Placeholder number ${i + 1}`, client_name: `Placeholder client ${i + 1}`,
    source: i % 3 === 2 ? 'upload' : 'clio', source_label: i % 3 === 2 ? 'Uploaded' : 'Imported', archived: false, stage: ['Intake', 'Treatment', 'Demand', 'Litigation'][i % 4],
    value: { ...f(`$${(i + 1) * 100},000`, 'assumed'), amount: (i + 1) * 100000 }, coverage: { ...f(`$${(i + 1) * 25},000`, i % 2 ? 'contested' : 'confirmed'), amount: (i + 1) * 25000 },
    spend: null, limitations: f(`2000-01-${String(i + 1).padStart(2, '0')}`, 'confirmed'),
    next_deadline: { case_id: 9000 + i, case_name: `Placeholder case ${i + 1}`, title: `Placeholder deadline ${i + 1}`, date: `2000-02-${String(i + 1).padStart(2, '0')}`, days_from_today: i + 3, kind: 'task', bucket: 'coming' },
    overdue: i % 3, waiting: i % 4, coming: 1, to_review: i % 5, last_activity: `2000-01-${String(i + 1).padStart(2, '0')}`,
    documents: 3, pages: 40, pages_read: i % 4 === 3 ? 12 : i % 6 === 5 ? 0 : 40,
    reading: i % 4 === 3 ? '12 of 40 pages read' : i % 6 === 5 ? 'not read yet' : '40 of 40 pages read', imported_at: null, agenda: [],
  }));
  return {
    generated_at: 'placeholder', cases, warnings: [],
    totals: { cases: n, open_cases: n, archived: 0, overdue: cases.reduce((a, c) => a + c.overdue, 0), waiting: cases.reduce((a, c) => a + c.waiting, 0), coming: n, due_14_days: n, to_review: cases.reduce((a, c) => a + c.to_review, 0), value_total: null, coverage_total: null, spend_total: null },
    agenda: cases.slice(0, 12).map((c, i) => ({ case_id: c.id, case_name: c.name, title: `Placeholder item ${i + 1}`, date: `2000-02-${String(i + 1).padStart(2, '0')}`, days_from_today: i + 3, kind: i % 2 ? 'calendar_entry' : 'task', bucket: 'coming' })),
  };
}

async function loadOverview(ctx) {
  if (MOCK === '1' || MOCK === 'empty') return mockOverview();
  return ctx.api('/api/firm/overview');
}

const pillCount = (ctx, n, cls) => (n ? ctx.pill(String(n), cls) : ctx.el('span', { class: 'muted', text: '–' }));
// The claim / source pattern on a row: the figure, then its first source (and how many more), or that there is none.
const srcLine = (ctx, f) => {
  const list = f?.sources || [];
  return list.length
    ? el('span', { class: 'cs-sources' }, ctx.chip(list[0]), list.length > 1 ? el('span', { class: 'cs-more', text: `+${list.length - 1}` }) : null)
    : el('span', { class: 'cs-none', text: 'no source in the file' });
};
const factText = (ctx, f) => (f ? (f.display ?? (f.amount != null ? ctx.fmt.money(f.amount) : null)) : null);

export default function mount(host, _c, ctx0) {
  const open = (id, tab) => (ctx0.openCase ? ctx0.openCase(id, tab) : ctx0.selectMatter?.(id));
  // the list engine keys its saved view by matterId and opens a row through ctx.openSource
  const ctx = { ...ctx0, matterId: 'firm', openSource: (ref) => open(ref.caseId) };
  let data = { cases: [], totals: null, agenda: [] };
  let showArchived = false;
  let view = (() => { try { return localStorage.getItem('v2:cases:view') === 'cards' ? 'cards' : 'table'; } catch { return 'table'; } })();
  let refreshList = null;
  let list = null;
  const cleanups = new Set();   // document listeners that must not outlive the view
  const snackHost = el('div', { class: 'cs-snacks', 'aria-live': 'polite' });

  const totalsEl = el('div', { class: 'cs-totals v2-figs' });
  const agendaEl = el('section', { class: 'cs-agenda' });
  const listHost = el('div', { class: 'cs-list' });
  const cardsHost = el('div', { class: 'cs-cards', hidden: true });
  const state = el('div', { class: 'cs-state', 'aria-live': 'polite' });

  const rows = () => data.cases.map((r) => normCase(r, data.agenda)).filter((r) => showArchived || !r.archived);

  function paintTotals() {
    const t = data.totals;
    const f = (label, v) => el('div', { class: 'v2-fig' }, el('span', { class: 'v2-fig-l', text: label }), el('span', { class: `v2-fig-v${v == null ? ' unk' : ''}`, text: v == null ? 'not available' : String(v) }));
    totalsEl.replaceChildren(
      f('Open cases', t?.open_cases),
      f('Items overdue', (t?.overdue ?? t?.overdue_items)),
      f('Deadlines in the next 14 days', (t?.due_14_days ?? t?.deadlines_next_14_days)),
      f('Differences to review', (t?.to_review ?? t?.differences_to_review)));
  }

  function paintAgenda() {
    const items = (data.agenda || []).slice(0, OPEN_AGENDA);
    agendaEl.replaceChildren(el('h3', { text: 'Due across your cases' }),
      items.length
        ? el('ul', { class: 'v2-list' }, items.map((a) => el('li', { class: 'v2-row cs-ag', tabindex: '0', role: 'button',
          onclick: () => open(a.case_id, a.kind === 'calendar_entry' ? 'calendar' : 'tasks'),
          onkeydown: (e) => { if (e.key === 'Enter') open(a.case_id, a.kind === 'calendar_entry' ? 'calendar' : 'tasks'); } },
        ctx.pill(a.case_name || 'Case', ''),
        el('span', { class: 'v2-row-title', text: a.title }),
        el('span', { class: 'v2-row-meta', text: [a.date ? ctx.fmt.date(a.date) : '', a.days_from_today != null ? ctx.fmt.days(a.days_from_today) : ''].filter(Boolean).join('  ·  ') }))))
        : el('p', { class: 'v2-empty', text: 'Nothing is dated across your cases yet.' }));
  }

  // ---- archive and restore: this product's own store only; Undo undoes it
  async function setArchived(r, archive) {
    try {
      await ctx.api(`/api/matters/${r.id}/${archive ? 'archive' : 'restore'}`, { method: 'POST' });
      const rec = data.cases.find((x) => x.id === r.id);
      if (rec) rec.archived = archive;
      refresh();
      snack(`${archive ? 'Archived' : 'Restored'}: ${r.name || r.client}.`, () => setArchived(r, !archive));
    } catch (err) { ctx.toast?.(`Not changed: ${err.message}`, 'error'); }
  }
  function snack(text, undo) {
    const bar = el('div', { class: 'cs-snack', role: 'status' }, el('span', { text }),
      el('button', { class: 'btn small', type: 'button', onclick: () => { bar.remove(); undo(); } }, 'Undo'));
    snackHost.replaceChildren(bar);
    setTimeout(() => bar.remove(), 10000);
  }

  const menu = (r) => {
    const b = el('button', { class: 'btn small quiet', type: 'button', 'aria-label': `Actions for ${r.name || r.client}`, text: '⋯' });
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const pop = el('div', { class: 'cs-pop', role: 'menu' },
        el('button', { class: 'btn small quiet', type: 'button', role: 'menuitem', onclick: () => { pop.remove(); open(r.id); } }, 'Open'),
        el('button', { class: 'btn small quiet', type: 'button', role: 'menuitem', onclick: () => { pop.remove(); setArchived(r, !r.archived); } }, r.archived ? 'Restore' : 'Archive'));
      b.parentElement.append(pop);
      const away = (ev) => { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('click', away, true); cleanups.delete(stop); } };
      const stop = () => document.removeEventListener('click', away, true);
      cleanups.add(stop);
      setTimeout(() => document.addEventListener('click', away, true), 0);
    });
    return el('span', { class: 'cs-menu' }, b);
  };

  const nameCell = (r) => el('div', { class: 'cs-name' },
    el('strong', { text: r.client || r.name || 'Untitled' }),
    r.client && r.name ? el('span', { class: 'muted small', text: r.name }) : null,
    r.number ? el('span', { class: 'muted small', text: r.number }) : null,
    r.archived ? ctx.pill('Archived', 'st-stale') : null,
    r.error ? el('span', { class: 'error small', text: 'This case could not be read.' }) : null);

  const table = listTab({
    id: 'cases', aiKind: 'row', title: 'Your cases', noun: 'cases',
    none: 'No cases match.',
    load: Object.assign(async (_c, { offset, limit, q }) => {
      const needle = (q || '').trim().toLowerCase();
      const all = rows().filter((r) => !needle || `${r.client} ${r.name} ${r.number} ${r.stage}`.toLowerCase().includes(needle));
      return { rows: all.slice(offset, offset + limit).map((r) => ({ ...r, caseId: r.id })), total: all.length, available: true };
    }, { haystack: (r) => `${r.client} ${r.name} ${r.number} ${r.stage}` }),
    source: (r) => ({ caseId: r.id }),
    columns: [
      { label: 'Case', key: 'case', cell: nameCell, cls: 'cs-case', sort: (r) => r.client || r.name },
      { label: 'Stage', key: 'stage', cell: (r) => r.stage || el('span', { class: 'muted', text: 'not set' }), cls: 'cs-n', sort: (r) => r.stage },
      { label: 'Value', key: 'value', cell: (r) => (r.value ? el('span', { class: 'cs-stack' }, factText(ctx, r.value) || 'not in the record', srcLine(ctx, r.value)) : 'not in the record'), cls: 'num cs-n', sort: (r) => r.value?.amount ?? null },
      { label: 'Coverage', key: 'coverage', cell: (r) => (r.coverage ? el('span', { class: 'cs-stack' }, el('span', {}, factText(ctx, r.coverage) || 'not in the record', ' ', r.coverage.status && r.coverage.status !== 'unknown' ? ctx.pill(r.coverage.status, `st-${r.coverage.status}`) : null), srcLine(ctx, r.coverage)) : 'not in the record') },
      { label: 'Next deadline', key: 'next', cls: 'cs-dl', cell: (r) => (r.next ? el('span', { class: 'cs-dlc' }, el('strong', { text: ctx.fmt.date(r.next.date) || '' }), el('span', { class: 'cs-dlt', title: r.next.title || '', text: r.next.title || '' })) : r.limitations ? `Limitations ${factText(ctx, r.limitations)}` : 'none dated'), sort: (r) => r.next?.date ?? null },
      { label: 'Needs attention', key: 'overdue', cell: (r) => (r.overdue || r.waiting || r.review
        ? el('span', { class: 'cs-pills' }, r.overdue ? ctx.pill(`${r.overdue} overdue`, 'bad') : null, r.waiting ? ctx.pill(`${r.waiting} waiting`, 'warn') : null, r.review ? ctx.pill(`${r.review} to review`, 'st-assumed') : null)
        : el('span', { class: 'muted', text: 'Nothing' })), sort: (r) => (r.overdue ?? 0) * 1000 + (r.waiting ?? 0) * 10 + (r.review ?? 0) },
      { label: 'Last activity', key: 'last', cell: (r) => ctx.fmt.date(r.last) || (r.importedAt ? `Imported ${ctx.fmt.date(r.importedAt)}` : el('span', { class: 'muted', text: 'no activity yet' })), cls: 'cs-n', sort: (r) => r.last },
      { label: 'Source', key: 'source', cell: (r) => el('span', { class: 'cs-src' }, sourceWord(r), el('span', { class: 'muted small', text: reading(r) })), cls: 'cs-n', sort: (r) => r.source },
      { label: '', key: 'menu', cell: menu },
    ],
    sort: { key: 'last', dir: 'desc' },
    facets: [
      { id: 'stage', label: 'Stage', value: (r) => r.stage },
      { id: 'source', label: 'Source', value: (r) => sourceWord(r) },
      { id: 'flag', label: 'Show', options: () => [{ key: 'overdue', label: 'Has overdue', test: (r) => r.overdue > 0 }] },
    ],
    toolbar: (_x, refresh) => {
      refreshList = refresh;
      const arch = el('button', { class: 'btn small quiet', type: 'button', 'aria-pressed': 'false', onclick: (e) => {
        showArchived = !showArchived; e.currentTarget.setAttribute('aria-pressed', String(showArchived)); e.currentTarget.textContent = showArchived ? 'Hide archived' : 'Show archived'; refresh();
      } }, 'Show archived');
      const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'View' },
        el('button', { type: 'button', 'aria-pressed': String(view === 'table'), onclick: (e) => setView('table', e.currentTarget) }, 'Table'),
        el('button', { type: 'button', 'aria-pressed': String(view === 'cards'), onclick: (e) => setView('cards', e.currentTarget) }, 'Cards'));
      const add = el('button', { class: 'btn primary', type: 'button', onclick: () => (ctx.openNewCase ? ctx.openNewCase() : ctx.toast?.('Creating a case is not available here yet.', 'error')) }, 'New case');
      return el('span', { class: 'cs-tools' }, arch, seg, add);
    },
  });

  function setView(v, btn) {
    view = v;
    btn.parentElement.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
    paintView();
  }
  function paintView() {
    listHost.hidden = !data.cases.length;
    listHost.classList.toggle('is-cards', view === 'cards');   // the header and toolbar stay; only the table steps aside
    cardsHost.hidden = view !== 'cards' || !data.cases.length;
    try { localStorage.setItem('v2:cases:view', view); } catch { /* storage may be blocked */ }
    if (view === 'cards') paintCards();
  }
  function paintCards() {
    const needle = (listHost.querySelector('.tab-search')?.value || '').trim().toLowerCase();
    cardsHost.replaceChildren(...rows().filter((r) => !needle || `${r.client} ${r.name} ${r.number} ${r.stage}`.toLowerCase().includes(needle)).map((r) => el('article', { class: 'cs-card', tabindex: '0', role: 'button', onclick: (e) => { if (!e.target.closest('button')) open(r.id); }, onkeydown: (e) => { if (e.key === 'Enter') open(r.id); } },
      el('div', { class: 'cs-card-h' }, nameCell(r), menu(r)),
      el('p', { class: 'muted small', text: [r.stage, sourceWord(r)].filter(Boolean).join('  ·  ') }),
      el('div', { class: 'v2-figs' },
        el('div', { class: 'v2-fig' }, el('span', { class: 'v2-fig-l', text: 'Value' }), el('span', { class: 'v2-fig-v sm', text: factText(ctx, r.value) || 'not in the record' })),
        el('div', { class: 'v2-fig' }, el('span', { class: 'v2-fig-l', text: 'Coverage' }), el('span', { class: 'v2-fig-v sm', text: factText(ctx, r.coverage) || 'not in the record' }))),
      el('div', { class: 'cs-card-f' }, r.overdue ? ctx.pill(`${r.overdue} overdue`, 'bad') : null, r.waiting ? ctx.pill(`${r.waiting} waiting`, 'warn') : null, r.review ? ctx.pill(`${r.review} to review`, 'st-assumed') : null, el('span', { class: 'muted small', text: reading(r) })))));
  }

  function refresh() {
    if (view === 'cards') paintCards();
    refreshList?.();
  }

  function paintState() {
    const n = rows().length;
    state.replaceChildren();
    if (!data.cases.length) {
      state.append(el('div', { class: 'cs-first' }, el('h3', { text: 'Create your first case' }),
        el('p', { class: 'muted', text: 'Cases you import or upload appear here, with their money, deadlines and what needs review.' }),
        el('button', { class: 'btn primary', type: 'button', onclick: () => (ctx.openNewCase ? ctx.openNewCase() : ctx.toast?.('Creating a case is not available here yet.', 'error')) }, 'New case')));
    }
    listHost.hidden = !data.cases.length;
    agendaEl.hidden = !data.cases.length;
    totalsEl.hidden = !data.cases.length;
    void n;
  }

  host.replaceChildren(totalsEl, state, listHost, cardsHost, agendaEl, snackHost);
  listHost.append(el('span', { class: 'v2-skel short' }));
  totalsEl.replaceChildren(...[0, 1, 2, 3].map(() => el('div', { class: 'v2-fig' }, el('span', { class: 'v2-skel fig' }))));

  loadOverview(ctx).then((res) => {
    data = { cases: res?.cases || [], totals: res?.totals || null, agenda: res?.agenda || [] };
    listHost.replaceChildren();
    list = table(listHost, null, ctx);
    listHost.querySelector('.tab-search')?.addEventListener('input', () => { if (view === 'cards') paintCards(); });
    paintTotals(); paintAgenda(); paintState(); paintView();
  }).catch((err) => {
    totalsEl.replaceChildren();
    listHost.replaceChildren();
    state.replaceChildren(el('p', { class: 'error', text: `Could not load your cases: ${err.message}` }));
  });
  return { destroy() { list?.destroy?.(); cleanups.forEach((f) => f()); cleanups.clear(); } };
}
