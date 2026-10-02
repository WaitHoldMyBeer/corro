// The server says whether this list exists for the matter; its note is shown when there is nothing to list.
import { listTab } from './_list.js';
import { remoteLoader } from './_remote.js';

const money = (v, ctx) => (v == null || v === '' ? '' : ctx.fmt.money(Number(v)) ?? String(v));

const amount = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));

export default listTab({
  id: 'bills', aiKind: 'bill', title: 'Bills', noun: 'bills',
  unavailable: 'This list is not available for this matter.',
  none: 'No bills are on file.',
  sort: { key: 'date', dir: 'desc' },
  facets: [{ id: 'state', label: 'State', value: (r) => r.meta?.state }],
  load: remoteLoader('bills'),
  source: (r) => r.source || null,
  columns: [
    { label: 'Issued', key: 'date', cls: 'nowrap', sort: (r) => r.date, cell: (r, ctx) => ctx.fmt.date(r.date) || '' },
    { label: 'Bill', sort: (r) => r.meta?.number || r.title, cell: (r) => r.meta?.number || r.title },
    { label: 'State', cls: 'nowrap', sort: (r) => r.meta?.state, cell: (r) => r.meta?.state || '' },
    { label: 'Due', cls: 'nowrap', sort: (r) => r.meta?.due, cell: (r, ctx) => ctx.fmt.date(r.meta?.due) || '' },
    { label: 'Total', cls: 'num', sort: (r) => amount(r.meta?.total), cell: (r, ctx) => money(r.meta?.total, ctx) },
    { label: 'Paid', cls: 'num', sort: (r) => amount(r.meta?.paid), cell: (r, ctx) => money(r.meta?.paid, ctx) },
    { label: 'Balance', cls: 'num', sort: (r) => amount(r.meta?.balance), cell: (r, ctx) => money(r.meta?.balance, ctx) },
  ],
});
