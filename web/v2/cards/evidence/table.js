// Template: a table card bound to any record list of the case model.
// Copy `makeTable({...})` into a new card to bind another list; the default
// export is one instance, the contacts on the matter.
import { customCard } from '../../js/cards.js';

export function makeTable({ id, title, group = 'People', source, fields, limit = 8, sort }) {
  const base = customCard({ kind: 'table', title, source, fields, limit, sort });
  if (base.error) throw new Error(`table card ${id}: ${base.error.join('; ')}`);
  const { custom, spec, ...card } = base;   // a template card is code, not a lawyer's custom card
  const root = source.split('.')[0];
  return {
    ...card, id, group, size: 'l', depends: [root],
    empty(c) {
      const v = source.split('.').reduce((o, k) => (o == null ? undefined : o[k]), c);
      return Array.isArray(v) && v.length ? null : 'Nothing in this list yet.';
    },
  };
}

export default makeTable({ id: 'contacts-table', title: 'People on the matter', source: 'contacts', fields: ['name', 'role_text', 'type'], limit: 8 });
