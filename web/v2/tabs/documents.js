import { listTab, localLoader } from './_list.js';
import { importantControl } from './_important.js';
import { docTitle } from './_names.js';
import { el } from '../../js/util.js';
import { uploadControl } from './_upload.js';
import { loadMarked } from '../cards/evidence/_important-api.js';

let marked = new Set();
const textLayer = (d) => (d.has_text_layer == null ? 'Not checked' : d.has_text_layer ? 'Has text' : 'Scanned image');
const origin = (d) => (d.origin === 'uploaded' || d.id < 0 ? 'Uploaded' : 'Imported');
const local = localLoader((c) => c.documents, (d) => `${d.name} ${d.folder || ''}`);

export default listTab({
  id: 'documents', aiKind: 'document', title: 'Documents', noun: 'documents',
  none: 'No documents are on file.',
  load: async (ctx, o) => {
    if (o.offset === 0) marked = await loadMarked(ctx).catch(() => new Set());
    return local(ctx, o);
  },
  toolbar: (ctx, refresh) => uploadControl(ctx, refresh),
  source: (d) => d.source,
  facets: [
    { id: 'text', label: 'Text layer', value: textLayer },
    { id: 'origin', label: 'Origin', value: origin },
  ],
  groups: [{ id: 'folder', label: 'folder', of: (d) => ({ key: d.folder || '', label: d.folder || 'No folder', order: d.folder || '\uffff' }) }],
  columns: [
    { label: 'Document', sort: (d) => docTitle(d.name), cell: (d) => el('span', { title: d.name, text: docTitle(d.name) }) },
    { label: 'Folder', sort: (d) => d.folder, cell: (d) => d.folder || '' },
    { label: 'Pages', cls: 'num', sort: (d) => d.page_count, cell: (d) => (d.page_count == null ? '' : String(d.page_count)) },
    { label: 'Text layer', cls: 'nowrap', sort: textLayer, cell: textLayer },
    { label: 'Received', cls: 'nowrap', sort: (d) => d.received_at, cell: (d, ctx) => ctx.fmt.date(d.received_at) || '' },
    { label: 'Origin', cls: 'nowrap', sort: origin, cell: origin },
    { label: 'Important', cls: 'nowrap', cell: (d, ctx) => importantControl(d, ctx, marked.has(d.id)) },
  ],
});
