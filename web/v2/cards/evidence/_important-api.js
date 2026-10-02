// The one place that knows the important-documents route.
// GET -> { slots, items: [{ document, chosen_by, origin, why, ... }], rejected }; PUT one change at a time.
const base = (ctx) => `/api/matters/${ctx.matterId}/important-documents`;
const put = (ctx, body) => ctx.api(base(ctx), { method: 'PUT', body });

export async function loadSlots(ctx) {
  const r = await ctx.api(base(ctx));
  return (r.items || []).slice(0, r.slots || 10).map((s) => ({
    id: s.document.id, origin: s.origin || null, uploaded: s.document_origin === 'uploaded' || s.document.id < 0, name: s.document.name, by: s.chosen_by === 'lawyer' ? 'lawyer' : 'ai',
    why: s.chosen_by === 'lawyer' ? null : (s.why || null), pages: s.document.page_count ?? null, source: s.document.source || null,
  }));
}
export const loadMarked = async (ctx) => new Set((await loadSlots(ctx)).filter((s) => s.by === 'lawyer').map((s) => s.id));
export const accept = (ctx, id) => put(ctx, { action: 'accept', document_id: id });
export const reject = (ctx, id) => put(ctx, { action: 'reject', document_id: id });
export const mark = (ctx, id) => put(ctx, { action: 'mark', document_id: id });
export const unmark = (ctx, id) => put(ctx, { action: 'unmark', document_id: id });
export const reorder = (ctx, ids) => put(ctx, { action: 'reorder', order: ids });
