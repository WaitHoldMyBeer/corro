import { listTab, localLoader } from './_list.js';

export default listTab({
  id: 'fields', aiKind: 'field', title: 'Fields', noun: 'fields',
  none: 'No fields are recorded on this matter.',
  load: localLoader((c) => c.custom_fields, (f) => `${f.label} ${f.display} ${f.detail || ''}`),
  source: (f) => f.sources?.[0] || null,
  columns: [
    { label: 'Field', sort: (f) => f.label, cell: (f) => f.label },
    { label: 'Value', sort: (f) => f.display, cell: (f) => f.display },
    { label: 'Last changed', cls: 'nowrap', sort: (f) => f.date, cell: (f, ctx) => ctx.fmt.date(f.date) || '' },
  ],
});
