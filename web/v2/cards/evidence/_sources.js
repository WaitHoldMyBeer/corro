// One pattern for "claim, source": a short run of source chips that open the page directly, then "+N".
// Class names are the shared ones from design.css (.cs-source); with none in the file it says so plainly.
export function sourceLine(ctx, refs, { max = 2, none = 'No source in the file' } = {}) {
  const list = (refs || []).filter((r) => r && r.href);
  const { el } = ctx;
  if (!list.length) return el('span', { class: 'cs-none', text: none });
  return el('span', { class: 'cs-sources' }, list.slice(0, max).map((r) => ctx.chip(r)),
    list.length > max ? el('span', { class: 'cs-more', title: `${list.length - max} more in the expanded view`, text: `+${list.length - max}` }) : null);
}
export const firstRef = (claimIds, claims) => {
  for (const id of claimIds || []) { const k = claims.get(id); if (k?.source?.href) return k.source; }
  return null;
};
export const allRefs = (claimIds, claims) => (claimIds || []).map((id) => claims.get(id)?.source).filter((r) => r && r.href);
