// The server says whether this list exists for the matter; its note is shown when there is nothing to list.
import { listTab } from './_list.js';
import { remoteLoader } from './_remote.js';

const money = (v, ctx) => (v == null || v === '' ? '' : ctx.fmt.money(Number(v)) ?? String(v));

const amount = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));

export default listTab({
  id: 'transactions', aiKind: 'row', title: 'Transactions', noun: 'transactions',
  unavailable: 'This list is not available for this matter.',
  none: 'No transactions are on file.',
  sort: { key: 'date', dir: 'desc' },
  facets: [{ id: 'type', label: 'Type', value: (r) => r.meta?.type }, { id: 'account', label: 'Account', value: (r) => r.meta?.account }],
  load: remoteLoader('transactions'),
  source: (r) => r.source || null,
  columns: [
    { label: 'Date', cls: 'nowrap', sort: (r) => r.date, cell: (r, ctx) => ctx.fmt.date(r.date) || '' },
    { label: 'Transaction', sort: (r) => r.title, cell: (r) => r.title },
    { label: 'Type', cls: 'nowrap', sort: (r) => r.meta?.type, cell: (r) => r.meta?.type || '' },
    { label: 'Account', sort: (r) => r.meta?.account, cell: (r) => r.meta?.account || '' },
    { label: 'Amount', cls: 'num', sort: (r) => amount(r.meta?.amount), cell: (r, ctx) => money(r.meta?.amount, ctx) },
  ],
});
