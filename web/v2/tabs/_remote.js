// Paged record lists from the server: GET /api/matters/{m}/records/{tab}.
// The route has no text filter, so a filter loads the whole list once (cached) and matches here.
const PAGE = 200;
const cache = new Map();

export function remoteLoader(...names) {
  let tab = names[0];
  const base = remoteLoaderFor;
  return async (ctx, o) => {
    try { return await base(() => tab)(ctx, o); } catch (err) {
      if (err.status === 404 && names.length > 1 && tab === names[0]) { tab = names[1]; return base(() => tab)(ctx, o); }
      throw err;
    }
  };
}

function remoteLoaderFor(getTab) {
  return async (ctx, { offset, limit, q }) => {
    const tab = getTab();
    const url = (o, l) => `/api/matters/${ctx.matterId}/records/${tab}?offset=${o}&limit=${l}`;
    if (!q) {
      const r = await ctx.api(url(offset, limit));
      if (!r.available) return { available: false, reason: r.note };
      return { rows: r.items, total: r.total, available: true, note: r.note };
    }
    const key = `${ctx.matterId}/${tab}`;
    let all = cache.get(key);
    if (!all || Date.now() - all.at > 60000) {
      const items = [];
      for (let o = 0; ; o += PAGE) {
        const r = await ctx.api(url(o, PAGE));
        if (!r.available) return { available: false, reason: r.note };
        items.push(...r.items);
        if (items.length >= r.total || !r.items.length) break;
      }
      all = { at: Date.now(), items };
      cache.set(key, all);
    }
    const n = q.trim().toLowerCase();
    const hit = all.items.filter((i) => `${i.title} ${i.snippet || ''} ${Object.values(i.meta || {}).join(' ')}`.toLowerCase().includes(n));
    return { rows: hit.slice(offset, offset + limit), total: hit.length, available: true };
  };
}
