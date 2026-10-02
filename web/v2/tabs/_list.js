// A filterable, sortable record list used by every record tab. The whole list is held here once it is
// loaded, so search, filter chips, sorting and grouping all happen in the browser; rows are drawn in
// chunks as the reader scrolls, so a few hundred rows stay smooth. Text goes in through textContent only.
import { el } from '../../js/util.js';
import { loadCss } from './_css.js';

loadCss('tabs.css');

const CHUNK = 60, PAGE = 200;

// Adapter for a list the case model already holds.
export function localLoader(getRows, haystack) {
  const load = async (ctx, { offset, limit, q, c }) => {
    const all = getRows(c) || [];
    const needle = (q || '').trim().toLowerCase();
    const rows = needle ? all.filter((r) => haystack(r).toLowerCase().includes(needle)) : all;
    return { rows: rows.slice(offset, offset + limit), total: rows.length, available: true };
  };
  load.haystack = haystack;
  return load;
}

// What a server record row is searched on when the tab gives no haystack of its own.
const recordText = (r) => `${r.title || ''} ${r.snippet || ''} ${Object.values(r.meta || {}).join(' ')}`;
const blank = (v) => v == null || v === '';

// Wraps each match of `needle` in <mark>, leaving controls alone.
function highlight(node, needle) {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  const hits = [];
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    if (!t.parentElement.closest('button,select,input,textarea,mark') && t.data.toLowerCase().includes(needle)) hits.push(t);
  }
  for (const t of hits) {
    const frag = document.createDocumentFragment();
    const low = t.data.toLowerCase();
    let at = 0;
    for (let i = low.indexOf(needle); i !== -1; i = low.indexOf(needle, at)) {
      frag.append(t.data.slice(at, i), el('mark', { text: t.data.slice(i, i + needle.length) }));
      at = i + needle.length;
    }
    frag.append(t.data.slice(at));
    t.replaceWith(frag);
  }
}

// cfg: { id, title, noun, columns:[{label, key?, cell(row, ctx) -> string|Node, cls, sort?(row) -> string|number|null}],
//        load(ctx, {offset,limit,q,c}), source(row) -> SourceRef|null, unavailable: sentence, none: sentence,
//        toolbar?(ctx, refresh) -> Node, haystack?(row) -> string,
//        sort?: {key, dir}, order?(a, b) -> number (the tab's own comparator; used only while the default sort is on),
//        facets?: [{id, label, value?(row) -> string, options?(rows, c) -> [{key, label, test(row)}], range?(row) -> 'YYYY-MM-DD'|null, rangeLabel?}],
//        groups?: [{id, label, of(row, c) -> {key, label, order}}] }
// A facet with `value` builds its chips from the distinct values in the rows; nothing is listed in code.
export function listTab(cfg) {
  const cols = cfg.columns.map((col) => ({ ...col, key: col.key || col.label.toLowerCase().replace(/\W+/g, '-') }));
  const dflt = cfg.sort ? { key: cfg.sort.key, dir: cfg.sort.dir || 'asc' } : null;
  return function mount(host, c, ctx, params = {}) {
    const model = () => ctx.caseModel || c;
    const hay = cfg.haystack || cfg.load.haystack || recordText;
    const noun = cfg.noun || 'rows';
    const facets = (cfg.facets || []).map((f) => ({ ...f, opts: [] }));
    const storeKey = `v2:list:${ctx.matterId}:${cfg.id}`;
    const st = { q: '', f: {}, range: {}, sort: dflt && { ...dflt }, group: '', shut: new Set() };
    let all = [], searched = [], shown = [], display = [], drawn = 0, gen = 0, emptyNote = '';

    // ---- state: the address wins, then what this browser last used
    const saved = Object.keys(params).length ? params : (() => { try { return JSON.parse(localStorage.getItem(storeKey)) || {}; } catch { return {}; } })();
    st.q = saved.q || '';
    for (const f of facets) {
      st.f[f.id] = new Set(String(saved[f.id] || '').split('~').filter(Boolean));
      if (f.range && (saved[`${f.id}_from`] || saved[`${f.id}_to`])) st.range[f.id] = { from: saved[`${f.id}_from`] || '', to: saved[`${f.id}_to`] || '' };
    }
    if (saved.sort) {
      const desc = saved.sort[0] === '-', key = desc ? saved.sort.slice(1) : saved.sort;
      if (cols.some((col) => col.key === key && col.sort)) st.sort = { key, dir: desc ? 'desc' : 'asc' };
    }
    if (cfg.groups?.some((g) => g.id === saved.group)) st.group = saved.group;

    function persist(store = true) {
      const p = {};
      for (const f of facets) {
        if (st.f[f.id].size) p[f.id] = [...st.f[f.id]].join('~');
        const r = st.range[f.id];
        if (r) { if (r.from) p[`${f.id}_from`] = r.from; if (r.to) p[`${f.id}_to`] = r.to; }
      }
      if (st.sort && !(dflt && st.sort.key === dflt.key && st.sort.dir === dflt.dir)) p.sort = (st.sort.dir === 'desc' ? '-' : '') + st.sort.key;
      if (st.group) p.group = st.group;
      if (store) { try { localStorage.setItem(storeKey, JSON.stringify(p)); } catch { /* private window: the address still carries the view */ } }
      if (st.q) p.q = st.q;
      // replaceState, not location.hash: the view becomes linkable without the shell mounting the tab again
      // the address may carry the case in front of the tab (#/c/<id>/tasks); keep whatever path is there
      const path = location.hash.replace(/^#\/?/, '').split('?')[0];
      const qs = new URLSearchParams(p).toString();
      const want = `#/${path}${qs ? `?${qs}` : ''}`;
      if (path.split('/').pop() === cfg.id && location.hash !== want) history.replaceState(null, '', want);
    }

    // ---- elements
    const count = el('span', { class: 'tab-count muted small', 'aria-live': 'polite' });
    const search = el('input', { type: 'search', class: 'tab-search', placeholder: `Search ${noun}`, 'aria-label': `Search ${noun}` });
    search.value = st.q;
    const groupBy = cfg.groups?.length ? el('select', { class: 'tab-groupby', 'aria-label': 'Group by' },
      el('option', { value: '', text: 'No grouping' }), cfg.groups.map((g) => el('option', { value: g.id, text: `Group by ${g.label}` }))) : null;
    if (groupBy) { groupBy.value = st.group; groupBy.addEventListener('change', () => { st.group = groupBy.value; st.shut.clear(); apply(); }); }
    const tbody = el('tbody');
    const headRow = el('tr');
    const table = el('table', { class: 'tab-table' }, el('thead', {}, headRow), tbody);
    const sentinel = el('div', { class: 'tab-sentinel', 'aria-hidden': 'true' });
    const note = el('div', { class: 'tab-note' });
    const wrap = el('div', { class: 'tab-scroll' }, table, sentinel);
    const bar = el('div', { class: 'tab-bar', hidden: true });
    const active = el('div', { class: 'tab-active', hidden: true });
    for (const f of facets) {
      f.chips = el('span', { class: 'tab-chips' });
      f.box = el('div', { class: 'tab-facet', role: 'group', 'aria-label': f.label }, el('span', { class: 'tab-facet-l', text: f.label }), f.chips);
      if (f.range) {
        f.from = el('input', { type: 'date', 'aria-label': `${f.rangeLabel || f.label} from` });
        f.to = el('input', { type: 'date', 'aria-label': `${f.rangeLabel || f.label} to` });
        f.bad = el('span', { class: 'error small', role: 'alert' });
        f.rangeBox = el('span', { class: 'tab-range', hidden: true }, `${f.rangeLabel || f.label} from`, f.from, 'to', f.to, f.bad);
        const changed = () => { st.range[f.id] = { from: f.from.value, to: f.to.value }; apply(); };
        f.from.addEventListener('input', changed); f.to.addEventListener('input', changed);
        f.box.append(f.rangeBox);
      }
      bar.append(f.box);
    }

    // ---- filtering, sorting, grouping
    const rangeOk = (r) => !(r.from && r.to && r.from > r.to);
    function pass(f, row) {
      const r = st.range[f.id];
      if (r) {
        if (!rangeOk(r)) return true;   // an impossible range filters nothing; the bar says why
        const d = f.range(row);
        return !!d && (!r.from || d >= r.from) && (!r.to || d <= r.to);
      }
      const on = st.f[f.id];
      return !on.size || f.opts.some((o) => on.has(o.key) && o.test(row));
    }
    function sorted(rows) {
      const col = st.sort && cols.find((x) => x.key === st.sort.key && x.sort);
      if (!col) return rows;
      const dir = st.sort.dir === 'desc' ? -1 : 1;
      const own = cfg.order && dflt && st.sort.key === dflt.key && st.sort.dir === dflt.dir;
      return rows.map((r, i) => [r, i, col.sort(r)]).sort(([a, i, x], [b, j, y]) => {
        if (own) return cfg.order(a, b) || i - j;
        if (blank(x) || blank(y)) return blank(x) && blank(y) ? i - j : blank(x) ? 1 : -1;   // no value: always last
        const d = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' });
        return d ? d * dir : i - j;
      }).map(([r]) => r);
    }
    function grouped(rows) {
      const g = cfg.groups?.find((x) => x.id === st.group);
      if (!g) return rows.map((row) => ({ row }));
      const sets = new Map();
      for (const row of rows) {
        const k = g.of(row, model());
        if (!sets.has(k.key)) sets.set(k.key, { ...k, rows: [] });
        sets.get(k.key).rows.push(row);
      }
      return [...sets.values()].sort((a, b) => (typeof a.order === 'number' && typeof b.order === 'number' ? a.order - b.order : String(a.order ?? a.label).localeCompare(String(b.order ?? b.label), undefined, { numeric: true })))
        .flatMap((s) => [{ group: s }, ...(st.shut.has(s.key) ? [] : s.rows.map((row) => ({ row })))]);
    }

    // ---- drawing
    function paintHead() {
      headRow.replaceChildren(...cols.map((col) => {
        const on = st.sort?.key === col.key;
        if (!col.sort) return el('th', { scope: 'col', class: col.cls || '', text: col.label });
        const b = el('button', { type: 'button', class: 'tab-sort', title: `Sort by ${col.label.toLowerCase()}` }, col.label, el('span', { class: 'tab-sort-i', 'aria-hidden': 'true', text: on ? (st.sort.dir === 'desc' ? '↓' : '↑') : '' }));
        b.addEventListener('click', () => { st.sort = { key: col.key, dir: on && st.sort.dir === 'asc' ? 'desc' : 'asc' }; apply(); headRow.querySelector(`[data-k="${col.key}"] button`)?.focus(); });
        return el('th', { scope: 'col', class: col.cls || '', 'data-k': col.key, 'aria-sort': on ? (st.sort.dir === 'desc' ? 'descending' : 'ascending') : 'none' }, b);
      }));
    }
    function drawMore() {
      const needle = st.q.trim().toLowerCase();
      for (const item of display.slice(drawn, drawn + CHUNK)) {
        if (item.group) {
          const s = item.group, open = !st.shut.has(s.key);
          const b = el('button', { type: 'button', class: 'tab-group-b', 'aria-expanded': String(open), 'data-k': `g:${s.key}` }, el('span', { 'aria-hidden': 'true', text: open ? '▾' : '▸' }), s.label, el('span', { class: 'tab-chip-n', text: String(s.rows.length) }));
          b.addEventListener('click', () => { if (open) st.shut.add(s.key); else st.shut.delete(s.key); redraw(); tbody.querySelector(`[data-k="${CSS.escape(`g:${s.key}`)}"]`)?.focus(); });
          tbody.append(el('tr', { class: 'tab-group' }, el('td', { colspan: String(cols.length) }, b)));
          continue;
        }
        const r = item.row, ref = cfg.source?.(r) || null;
        const tr = el('tr', { 'data-ai-unit': '', 'data-ai-kind': cfg.aiKind || 'row', 'data-record-id': r.id ?? null, 'data-ai-ref': ref ? JSON.stringify(ref) : null, class: ref ? 'tab-row open' : 'tab-row', tabindex: ref ? '0' : null, title: ref ? 'Open the source' : null },
          cols.map((col) => el('td', { class: col.cls || '' }, col.cell(r, ctx))));
        if (needle) highlight(tr, needle);
        if (ref) {
          tr.addEventListener('click', (e) => { if (!e.target.closest('button,a,input,select')) ctx.openSource(ref); });
          tr.addEventListener('keydown', (e) => {
            if (e.target !== tr) return;
            if (e.key === 'Enter') ctx.openSource(ref);
            else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              let to = tr;
              do { to = e.key === 'ArrowDown' ? to.nextElementSibling : to.previousElementSibling; } while (to && !to.matches('.tab-row.open'));
              if (to) { e.preventDefault(); to.focus(); }
            }
          });
        }
        tbody.append(tr);
      }
      drawn = Math.min(display.length, drawn + CHUNK);
      // A short first chunk can leave the sentinel in view with more waiting.
      if (drawn < display.length && sentinel.getBoundingClientRect().top < window.innerHeight + 200) drawMore();
    }
    function redraw() { display = grouped(shown); drawn = 0; tbody.replaceChildren(); drawMore(); }

    function chip(label, n, on, k, toggle) {
      const b = el('button', { type: 'button', class: 'btn small tab-chip', 'aria-pressed': String(on), 'data-k': k, title: label.length > 28 ? label : null }, el('span', { text: label }), n == null ? null : el('span', { class: 'tab-chip-n', text: String(n) }));
      b.addEventListener('click', toggle);
      return b;
    }
    function paintBar() {
      const focused = bar.contains(document.activeElement) ? document.activeElement.getAttribute('data-k') : null;
      const pills = [];
      let any = false;
      for (const f of facets) {
        const others = searched.filter((r) => facets.every((x) => x === f || pass(x, r)));
        const on = st.f[f.id], ranged = !!st.range[f.id];
        // a chip that would show nothing is left out, unless it is the one switched on
        const chips = f.opts.map((o) => [o, others.filter(o.test).length]).filter(([o, n]) => n > 0 || on.has(o.key))
          .map(([o, n]) => chip(o.label, n, on.has(o.key), `${f.id}:${o.key}`, () => { if (on.has(o.key)) on.delete(o.key); else on.add(o.key); delete st.range[f.id]; apply(); }));
        if (f.range && all.some((r) => f.range(r))) {
          chips.push(chip('Custom range', null, ranged, `${f.id}:range`, () => {
            if (ranged) delete st.range[f.id];
            else {
              // start from everything that carries a date, so the first thing the range does is narrow
              const days = all.map((r) => f.range(r)).filter(Boolean).sort();
              on.clear(); st.range[f.id] = { from: days[0] || '', to: days[days.length - 1] || '' };
            }
            apply();
          }));
          f.rangeBox.hidden = !ranged;
          if (ranged) {
            const r = st.range[f.id];
            if (f.from.value !== r.from) f.from.value = r.from;
            if (f.to.value !== r.to) f.to.value = r.to;
            f.bad.textContent = rangeOk(r) ? '' : 'The first date is after the second, so the range is not applied.';
            if (rangeOk(r) && (r.from || r.to)) pills.push([`${f.rangeLabel || f.label}: ${r.from || 'any'} to ${r.to || 'any'}`, () => { delete st.range[f.id]; }]);
          }
        }
        for (const o of f.opts) if (on.has(o.key)) pills.push([`${f.label}: ${o.label}`, () => on.delete(o.key)]);
        f.chips.replaceChildren(...chips);
        f.box.hidden = chips.length < 2 && !on.size && !ranged;
        any = any || !f.box.hidden;
      }
      if (st.q.trim()) pills.unshift([`Search: ${st.q.trim()}`, () => { st.q = ''; search.value = ''; }]);
      bar.hidden = !any;
      active.hidden = !pills.length;
      active.replaceChildren(...pills.map(([text, off]) => el('button', { type: 'button', class: 'pill tab-pill', title: 'Remove this filter', 'aria-label': `Remove filter ${text}`, onclick: () => { off(); apply(); } }, text, el('span', { 'aria-hidden': 'true', text: '×' }))),
        pills.length ? el('button', { type: 'button', class: 'linkish small', onclick: clear, text: pills.length > 1 ? 'Clear all' : 'Clear' }) : null);
      if (focused) bar.querySelector(`[data-k="${CSS.escape(focused)}"]`)?.focus();
    }
    function clear() {
      st.q = ''; search.value = '';
      for (const f of facets) { st.f[f.id].clear(); delete st.range[f.id]; }
      apply();
    }

    function apply(save = true) {
      const needle = st.q.trim().toLowerCase();
      searched = needle ? all.filter((r) => hay(r).toLowerCase().includes(needle)) : all;
      shown = sorted(searched.filter((r) => facets.every((f) => pass(f, r))));
      paintHead(); paintBar(); redraw();
      search.hidden = !all.length; if (groupBy) groupBy.hidden = !all.length;   // nothing to search in an empty list
      count.textContent = all.length ? `${shown.length} of ${all.length} ${noun}` : '';   // an empty list is said once, by the note below
      if (shown.length) note.replaceChildren();
      else if (all.length) note.replaceChildren(el('div', { class: 'empty' }, el('p', { text: `None of the ${all.length} ${noun} match these filters.` }), el('button', { type: 'button', class: 'btn', onclick: clear, text: 'Clear filters' })));
      else note.replaceChildren(el('p', { class: 'empty', text: emptyNote || cfg.none || 'Nothing on file.' }));
      if (save) persist();
    }

    async function load() {
      const mine = ++gen;
      // the same shape as the rows that replace it
      tbody.replaceChildren(...Array.from({ length: 6 }, () => el('tr', { class: 'tab-skel', 'aria-hidden': 'true' }, cols.map(() => el('td', {}, el('span', { class: 'v2-skel short' }))))));
      paintHead(); wrap.hidden = false; wrap.setAttribute('aria-busy', 'true'); note.replaceChildren();
      try {
        const rows = [];
        for (let offset = 0; ;) {
          const res = await cfg.load(ctx, { offset, limit: PAGE, q: '', c: model() });
          if (mine !== gen) return;
          if (res.available === false) {
            wrap.hidden = true; bar.hidden = true; active.hidden = true; search.hidden = true; if (groupBy) groupBy.hidden = true; count.textContent = '';
            note.replaceChildren(el('p', { class: 'empty', text: res.reason || cfg.unavailable || 'This list is not available for this matter.' }));
            return;
          }
          rows.push(...res.rows); offset += res.rows.length; emptyNote = res.note || emptyNote;
          if (!res.rows.length || offset >= (res.total ?? offset)) break;
        }
        all = rows;
        for (const f of facets) {
          if (f.options) f.opts = f.options(all, model());
          else {
            const seen = [...new Set(all.map((r) => f.value(r)).filter((v) => !blank(v)))].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
            f.opts = seen.length <= 8 ? seen.map((v) => ({ key: String(v), label: String(v), test: (r) => f.value(r) === v })) : [];   // chips only where a column has few distinct values
          }
          for (const k of [...st.f[f.id]]) if (!f.opts.some((o) => o.key === k)) st.f[f.id].delete(k);   // a remembered chip that is no longer in the rows
        }
        apply(false);
        persist(false);   // a view restored from this browser shows in the address too, so it can be copied as a link
      } catch (err) {
        if (mine !== gen) return;
        tbody.replaceChildren(); wrap.hidden = true;
        note.replaceChildren(el('div', { class: 'empty' }, el('p', { class: 'error', text: `Could not load this list: ${err.message}` }), el('button', { type: 'button', class: 'btn', onclick: load, text: 'Try again' })));
      } finally { if (mine === gen) wrap.removeAttribute('aria-busy'); }
    }

    let timer;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = search.value; apply(); }, 120); });
    search.addEventListener('keydown', (e) => { if (e.key === 'Escape' && search.value) { search.value = ''; st.q = ''; apply(); } });
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting) && drawn < display.length) drawMore(); }, { rootMargin: '300px' });
    io.observe(sentinel);
    wrap.addEventListener('scroll', () => { if (drawn < display.length && wrap.scrollTop + wrap.clientHeight > wrap.scrollHeight - 600) drawMore(); }, { passive: true });

    const sec = el('section', { class: 'tab' },
      el('header', { class: 'tab-h' }, el('h2', { text: cfg.title }), count, groupBy, search, cfg.toolbar?.(ctx, load)),
      bar, active, note, wrap);
    host.replaceChildren(sec);
    // The table scrolls inside the page so its header stays in view; the space above it is measured, not assumed.
    const fit = () => {
      const below = parseFloat(getComputedStyle(sec.closest('#v2-view') || sec).paddingBottom) || 32;
      sec.style.maxHeight = `max(360px, calc(100vh - ${Math.round(sec.getBoundingClientRect().top + window.scrollY + below)}px))`;
    };
    fit(); window.addEventListener('resize', fit);
    load();
    return { destroy: () => { io.disconnect(); clearTimeout(timer); window.removeEventListener('resize', fit); gen++; } };
  };
}
