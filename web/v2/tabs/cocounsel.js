// The server says whether this list exists for the matter; when it does not, its note is shown.
import { listTab } from './_list.js';
import { remoteLoader } from './_remote.js';

export default listTab({
  id: 'cocounsel', aiKind: 'contact', title: 'Co-counsel', noun: 'entries',
  unavailable: 'This list is not available for this matter.',
  none: 'Nothing on file.',
  load: remoteLoader('cocounsel', 'co-counsel'),
  source: (r) => r.source || null,
  columns: [
    { label: 'Date', cls: 'nowrap', sort: (r) => r.date, cell: (r, ctx) => ctx.fmt.date(r.date) || '' },
    { label: 'Item', sort: (r) => r.title, cell: (r) => r.title },
    { label: 'Detail', cell: (r) => r.snippet || '' },
  ],
});
