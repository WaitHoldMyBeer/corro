// Money lines on the matter: expenses, and each provider's charges as the server totals them.
import { listTab, localLoader } from './_list.js';
import { el } from '../../js/util.js';

const rows = (c) => [
  ...(c.spend?.lines || []).map((l) => ({ kind: 'Expense', dated: 'expense date', when: l.date, what: l.note || l.category_name || '', who: l.category_name || '', amount: l.amount, source: l.source })),
  ...(c.providers || []).filter((p) => p.bills?.line_count).map((p) => ({
    kind: 'Provider charges', dated: 'last service date', when: p.bills.last_service_date, what: `${p.bills.line_count} charge lines`, who: p.contact.name, amount: p.bills.billed_total, source: p.bills.sources?.[0] || null })),
];

export default listTab({
  id: 'activities', aiKind: 'expense', title: 'Activities', noun: 'entries',
  none: 'No expenses or provider charges are on file.',
  load: localLoader(rows, (r) => `${r.kind} ${r.what} ${r.who}`),
  source: (r) => r.source,
  sort: { key: 'date', dir: 'desc' },
  facets: [{ id: 'type', label: 'Type', value: (r) => r.kind }],
  columns: [
    { label: 'Type', cls: 'nowrap', sort: (r) => r.kind, cell: (r) => r.kind },
    // the date says what it is: an expense has its own date, a provider's charges show the last date of service
    { label: 'Date', cls: 'nowrap', sort: (r) => r.when, cell: (r, ctx) => (r.when ? el('span', {}, ctx.fmt.date(r.when), el('span', { class: 'tab-when', text: r.dated })) : '') },
    { label: 'Detail', sort: (r) => r.what, cell: (r) => r.what },
    { label: 'From', cls: 'tab-name', sort: (r) => r.who, cell: (r) => r.who },
    { label: 'Amount', cls: 'num', sort: (r) => r.amount, cell: (r, ctx) => (r.amount == null ? 'Not in the file' : ctx.fmt.money(r.amount)) },
  ],
});
