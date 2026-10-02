// The dashboard: search, the graph, and a grid of cards the lawyer can reorder, remove, add and expand.
import { el } from '../../js/util.js';
import { api } from '../../js/api.js';
import { loadCards, registry, renderCard, customCard, validateSpec, GROUPS } from './cards.js';
import { mountSearch } from './search.js';

// Readable in 30 seconds. A card that is not registered (yet) is skipped, never an error.
export const DEFAULT_LAYOUT = ['case-glance', 'money', 'next-moves', 'overdue', 'waiting', 'coming-up', 'for-review', 'important-documents'];
const DESCRIBE_ROUTE = (m) => `/api/matters/${m}/cards/design`;   // checker's endpoint: { prompt } -> { spec?, template?, error? }

// ---- layout persistence: the server when it has the route, local storage otherwise

const lsKey = (m) => `v2:dashboard:${m}`;
const libKey = (m) => `v2:library:${m}`;
const lsGet = (m, key = lsKey(m)) => { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; } };
const lsSet = (m, v, key = lsKey(m)) => { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage may be blocked */ } };

// The layout and the lawyer's library of saved cards live in one per-matter document on the server. The library is
// only sent when the server's GET carried a `saved` list, so an older server never sees a field it would refuse.
async function loadState(m) {
  let layout = null, saved = null, serverSaved = false;
  try {
    const got = await api(`/api/matters/${m}/dashboard`);   // { cards: [{id, size?, spec?}], saved?: [...], stored }
    if (got?.stored && Array.isArray(got.cards) && got.cards.length) layout = got.cards.map(fromServer);
    if (Array.isArray(got?.saved)) { saved = got.saved; serverSaved = true; }
  } catch { /* no route yet, or not allowed: fall back */ }
  if (!layout) layout = lsGet(m);
  const local = lsGet(m, libKey(m)) || [];
  if (!serverSaved) saved = local;
  else if (!saved.length && local.length) saved = local;   // adopt what this browser already had
  return { layout, saved, serverSaved };
}
let saveTimer = null;
function saveState(m, layout, saved, serverSaved) {
  lsSet(m, layout); lsSet(m, saved, libKey(m));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api(`/api/matters/${m}/dashboard`, { method: 'PUT', body: { cards: layout.map(toServer), ...(serverSaved ? { saved } : {}) } }).catch(() => {}), 400);
}

const fromServer = (x) => {
  const s = x.spec;
  if (s && s.base) return { id: x.id, base: s.base, settings: s.settings || {} };
  return { id: x.id, ...(s ? { custom: s } : {}) };
};
const toServer = (e) => ({ id: e.id, ...(e.custom ? { spec: e.custom } : e.base ? { spec: { base: e.base, settings: e.settings || {} } } : {}) });

const optionsOf = (s, c) => (typeof s.options === 'function' ? s.options(c) : (s.options || [])) || [];

const cardFor = (entry, c) => {
  if (entry.custom) { const k = customCard(entry.custom); return k.error ? null : { ...k, id: entry.id }; }
  if (entry.base) {
    const base = registry.cards.get(entry.base);
    if (!base) return null;
    const first = (base.settings || []).map((s) => optionsOf(s, c).find((o) => o.value === (entry.settings || {})[s.key])?.label).filter(Boolean)[0];
    return { ...base, id: entry.id, settings: entry.settings || {}, title: first ? `${base.title}: ${first}` : base.title };
  }
  return registry.cards.get(entry.id) || null;
};

export async function renderDashboard(host, state) {
  const { ctx, matterId } = state;
  await loadCards();
  const st = await loadState(matterId);
  let layout = st.layout || DEFAULT_LAYOUT.map((id) => ({ id }));
  layout = layout.filter(Boolean).map((e) => (typeof e === 'string' ? { id: e } : e)).filter((e) => e && e.id);
  let library = st.saved || [];
  let destroyed = false;
  const c0 = () => state.caseModel;

  const searchHost = el('div', { class: 'v2-search' });
  const graphSearch = el('div', { class: 'v2-search v2-gsearch' });
  const hero = el('div', { class: 'v2-hero' }, el('div', { class: 'v2-hero-empty', text: 'Loading the graph...' }));
  const grid = el('div', { class: 'v2-grid' });
  const bar = el('div', { class: 'v2-dash-bar' }, el('h2', { text: 'Your dashboard' }), el('span', { class: 'grow' }),
    el('button', { class: 'btn', type: 'button', onclick: () => openGallery() }, 'Add a card'),
    el('button', { class: 'btn ghost', type: 'button', onclick: () => { layout = DEFAULT_LAYOUT.map((id) => ({ id })); persist(); paintGrid(); } }, 'Reset'));
  host.replaceChildren(searchHost, graphSearch, hero, bar, grid);
  // dropping on empty space in the grid (the add tile may be hidden by the design) moves a card to the end
  grid.addEventListener('dragover', (e) => e.preventDefault());
  grid.addEventListener('drop', (e) => {
    e.preventDefault();
    const from = e.dataTransfer.getData('text/plain');
    const moved = layout.find((x) => x.id === from);
    if (!moved) return;
    layout = [...layout.filter((x) => x.id !== from), moved];
    persist(); paintGrid();
  });
  const search = mountSearch(searchHost, c0, ctx);
  const onKey = (e) => { if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) { e.preventDefault(); (graph?.focus ? graph.focus() : search.focus()); } };
  document.addEventListener('keydown', onKey);

  let graph = null;
  // The graph module draws its own search, legend and frame, so ours steps aside once it is up.
  import('./graph/index.js').then((m) => {
    if (destroyed) return;
    hero.replaceChildren();
    hero.classList.add('has-graph');
    graph = m.mountGraph(hero, { matterId, api, openSource: (ref, title) => ctx.openSources([ref], title), compact: true }) || null;
    searchHost.hidden = true; graphSearch.hidden = true;
  }).catch(() => { hero.replaceChildren(el('div', { class: 'v2-hero-empty', text: 'The graph is being built.' })); });

  const persist = () => saveState(matterId, layout, library, st.serverSaved);
  const bodies = new Map();   // entry id -> { card, body }

  // ---- the lawyer's library of saved cards: removing a card from the dashboard never removes it from here
  const stamp = () => Date.now().toString(36);
  const tplOf = (s) => (s.spec?.base ? s.spec : s.base ? { base: s.base, settings: s.settings } : null);   // older local items kept base flat
  const entryFromSaved = (s) => { const t = tplOf(s); return t ? { id: `${t.base}:${stamp()}`, base: t.base, settings: t.settings || {} } : { id: `custom-${stamp()}`, custom: s.spec }; };
  function saveToLibrary(entry, title, prompt) {
    library.unshift({ id: `lib-${stamp()}`, title: String(title).slice(0, 120), prompt: prompt ? String(prompt).slice(0, 2000) : null, created_at: new Date().toISOString(),
      spec: entry.base ? { base: entry.base, settings: entry.settings || {} } : entry.custom });
    persist();
  }

  // Every card is a pickable unit for the assistant: its id, title and the source refs it rendered.
  function paintBody(entry, card, body) {
    const refs = [];
    const track = { ...ctx, chip: (ref) => { if (ref) refs.push(ref); return ctx.chip(ref); }, chips: (rs) => { (rs || []).slice(0, 3).forEach((x) => x && refs.push(x)); return ctx.chips(rs); } };
    const r = renderCard(card, c0(), track, 'summary');
    body.replaceChildren(r.node || '');
    const frame = body.closest('.v2-card');
    if (frame) {
      frame.dataset.aiUnit = entry.id; frame.dataset.aiKind = 'card'; frame.dataset.aiTitle = card.title;
      const slim = refs.slice(0, 12).map((x) => ({ kind: x.kind, clio_id: x.clio_id, label: x.label, href: x.href, page: x.page ?? null }));
      frame.dataset.aiRef = JSON.stringify(slim);
    }
    return r.state;
  }

  function cardEl(entry, card) {
    const body = el('div', { class: 'v2-card-body' });
    const el0 = el('section', { class: `v2-card ${card.size}`, 'data-id': entry.id });
    const grip = el('span', { class: 'grip', title: 'Drag to reorder', 'aria-hidden': 'true', text: '⋮⋮' });
    grip.addEventListener('mousedown', () => { el0.draggable = true; });
    const hasDetail = !!card.detail;
    el0.append(el('div', { class: 'v2-card-h' }, grip, el('h3', { text: card.title, title: card.title }),
      entry.base ? el('button', { class: 'icon-btn', type: 'button', title: 'Change what this card shows', onclick: () => openSettings(registry.cards.get(entry.base), entry.settings || {}, (s) => { entry.settings = s; persist(); paintGrid(); }) }, 'Settings') : null,
      hasDetail || card.summary ? el('button', { class: 'icon-btn', type: 'button', title: 'Expand', 'aria-label': `Expand ${card.title}`, onclick: () => expand(card) }, 'Expand') : null,
      el('button', { class: 'icon-btn', type: 'button', title: 'Remove from the dashboard', 'aria-label': `Remove ${card.title}`, onclick: () => { layout = layout.filter((e) => e.id !== entry.id); persist(); paintGrid(); } }, 'Remove')), body);
    el0.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', entry.id); e.dataTransfer.effectAllowed = 'move'; el0.classList.add('dragging'); });
    el0.addEventListener('dragend', () => { el0.draggable = false; el0.classList.remove('dragging'); grid.querySelectorAll('.over').forEach((n) => n.classList.remove('over')); });
    el0.addEventListener('dragover', (e) => { e.preventDefault(); el0.classList.add('over'); });
    el0.addEventListener('dragleave', () => el0.classList.remove('over'));
    el0.addEventListener('drop', (e) => {
      e.preventDefault(); e.stopPropagation(); el0.classList.remove('over');
      const from = e.dataTransfer.getData('text/plain');
      if (!from || from === entry.id) return;
      const moved = layout.find((x) => x.id === from);
      if (!moved) return;
      layout = layout.filter((x) => x.id !== from);
      layout.splice(layout.findIndex((x) => x.id === entry.id), 0, moved);
      persist(); paintGrid();
    });
    return { el: el0, body };
  }

  function paintGrid() {
    bodies.clear();
    const nodes = [];
    for (const entry of layout) {
      const card = cardFor(entry, c0());
      if (!card) continue;
      const { el: node, body } = cardEl(entry, card);
      if (paintBody(entry, card, body) === 'hidden') continue;
      bodies.set(entry.id, { card, body });
      nodes.push(node);
    }
    const addTile = el('button', { class: 'v2-add v2-card s', type: 'button', onclick: () => openGallery() }, '+ Add a card');
    // dropping a card on the add tile moves it to the end
    addTile.addEventListener('dragover', (e) => { e.preventDefault(); addTile.classList.add('over'); });
    addTile.addEventListener('dragleave', () => addTile.classList.remove('over'));
    addTile.addEventListener('drop', (e) => {
      e.preventDefault(); addTile.classList.remove('over');
      const from = e.dataTransfer.getData('text/plain');
      const moved = layout.find((x) => x.id === from);
      if (!moved) return;
      layout = [...layout.filter((x) => x.id !== from), moved];
      persist(); paintGrid();
    });
    nodes.push(addTile);
    grid.replaceChildren(...nodes);
  }

  // A template is added, or edited, with a choice per setting. Options come from the matter itself.
  function openSettings(card, current, done, offerKeep = false) {
    const dlg = el('dialog', { class: 'v2-dlg', style: 'width:min(520px,94vw)' });
    const keep = el('input', { type: 'checkbox' });
    const selects = (card.settings || []).map((s) => {
      const opts = optionsOf(s, c0());
      const sel = el('select', { 'aria-label': s.label }, opts.map((o) => el('option', { value: o.value, text: o.label })));
      sel.value = current[s.key] ?? opts[0]?.value ?? '';
      return { s, sel, field: el('label', { class: 'v2-field' }, el('span', { class: 'small muted', text: s.label }), sel) };
    });
    dlg.append(el('div', { class: 'v2-dlg-h' }, el('h2', { text: card.title }), el('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close() }, 'Close')),
      el('div', { class: 'v2-dlg-b' }, ...selects.map((x) => x.field),
        offerKeep ? el('label', { class: 'small' }, keep, ' Keep this card in "Your cards"') : null,
        el('div', { class: 'row' }, el('button', { class: 'btn primary', type: 'button', onclick: () => {
          done(Object.fromEntries(selects.map(({ s, sel }) => [s.key, sel.value])), keep.checked); dlg.close();
        } }, 'Save'))));
    dlg.addEventListener('close', () => dlg.remove());
    document.body.append(dlg); dlg.showModal();
  }

  function expand(card) {
    const dlg = el('dialog', { class: 'v2-dlg' });
    const r = renderCard(card, c0(), ctx, card.detail ? 'detail' : 'summary');
    dlg.append(el('div', { class: 'v2-dlg-h' }, el('h2', { text: card.title }), el('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close() }, 'Close')), el('div', { class: 'v2-dlg-b' }, r.node || ''));
    dlg.addEventListener('close', () => dlg.remove());
    document.body.append(dlg); dlg.showModal();
  }

  function openGallery() {
    const dlg = el('dialog', { class: 'v2-dlg' });
    const close = () => dlg.close();
    const onList = () => new Set(layout.map((e) => e.id));
    const galleryBody = el('div', { class: 'v2-dlg-b' });
    function paint() {
      const have = onList();
      const groups = new Map();
      for (const card of registry.cards.values()) { const g = card.group || 'Case'; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(card); }
      const order = [...GROUPS.filter((g) => groups.has(g))];
      const describe = describeBox(dlg, () => { paint(); paintGrid(); });
      galleryBody.replaceChildren(...[describe, libraryBlock(paint), ...order.map((g) => el('div', { class: 'v2-gal-group' }, el('h3', { text: g }),
        el('div', { class: 'v2-gal-items' }, groups.get(g).map((card) => el('button', { type: 'button', class: 'v2-gal-item', disabled: !card.template && have.has(card.id),
          onclick: () => {
            if (card.template) {
              openSettings(card, {}, (settings, keep) => {
                const entry = { id: `${card.id}:${Date.now().toString(36)}`, base: card.id, settings };
                layout.push(entry);
                if (keep) { const k = cardFor(entry, c0()); saveToLibrary(entry, k?.title || card.title, ''); } else persist();
                paintGrid(); paint();
              }, true);
              return;
            }
            layout.push({ id: card.id }); persist(); paintGrid(); paint();
          } },
        el('strong', { text: card.title }), el('span', { class: 'small muted', text: !card.template && have.has(card.id) ? 'On your dashboard' : card.template ? 'Template: choose what it shows' : `${{ s: 'Small', m: 'Wide', l: 'Full width' }[card.size]} card` })))))),
        registry.cards.size ? null : el('p', { class: 'v2-empty', text: 'No cards are available yet.' })].filter(Boolean));
    }
    dlg.append(el('div', { class: 'v2-dlg-h' }, el('h2', { text: 'Add a card' }), el('button', { class: 'btn ghost', type: 'button', onclick: close }, 'Close')), galleryBody);
    dlg.addEventListener('close', () => dlg.remove());
    paint();
    document.body.append(dlg); dlg.showModal();
  }

  function libraryBlock(repaint) {
    if (!library.length) return null;
    const items = library.map((s) => {
      const card = cardFor(entryFromSaved(s), c0());
      const prev = el('div', { class: 'v2-lib-prev' });
      if (card) { const r = renderCard(card, c0(), ctx, 'summary'); prev.append(r.node || ''); }
      const title = el('strong', { text: s.title });
      const rename = el('button', { class: 'btn small ghost', type: 'button' }, 'Rename');
      rename.addEventListener('click', () => {
        const input = el('input', { type: 'text', value: s.title, maxlength: 80, 'aria-label': 'Card name' });
        title.replaceWith(input); input.focus(); input.select();
        const done = () => { const v = input.value.trim(); if (v) { s.title = v; persist(); } repaint(); };
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(); if (e.key === 'Escape') repaint(); });
        input.addEventListener('blur', done);
      });
      return el('div', { class: 'v2-lib-item' },
        el('div', { class: 'v2-lib-h' }, title, el('span', { class: 'grow' }), rename,
          el('button', { class: 'btn small danger', type: 'button', onclick: () => { library = library.filter((x) => x.id !== s.id); persist(); repaint(); } }, 'Delete')),
        s.prompt ? el('p', { class: 'small muted', text: s.prompt }) : null, prev,
        el('button', { class: 'btn small primary', type: 'button', disabled: !card, onclick: () => { layout.push(entryFromSaved(s)); persist(); paintGrid(); repaint(); } }, card ? 'Add to my dashboard' : 'Not available'));
    });
    return el('div', { class: 'v2-gal-group' }, el('h3', { text: 'Your cards' }), el('div', { class: 'v2-lib' }, items));
  }

  // "Describe a card": the server turns the words into a spec; the browser validates it and previews it.
  function describeBox(dlg, after) {
    const ta = el('textarea', { rows: 2, placeholder: 'Describe a card, for example: the five newest documents with their dates', 'aria-label': 'Describe a card', maxlength: 400 });
    const go = el('button', { class: 'btn primary', type: 'button' }, 'Preview');
    const msg = el('span', { class: 'small muted', role: 'status' });
    const preview = el('div', { class: 'v2-preview', hidden: true });
    let pending = null;
    go.addEventListener('click', async () => {
      const description = ta.value.trim();
      if (!description) { msg.textContent = 'Write what you want the card to show.'; return; }
      go.disabled = true; msg.textContent = 'Working...'; preview.hidden = true; pending = null;
      try {
        const res = await api(DESCRIBE_ROUTE(matterId), { method: 'POST', body: { prompt: description } });
        if (res.error) throw new Error(res.error);
        // A ready-made template that fits wins; otherwise the generic card built from the validated spec.
        let card, entry;
        if (res.template && registry.cards.get(res.template.base)?.template) {
          entry = { id: `${res.template.base}:${Date.now().toString(36)}`, base: res.template.base, settings: res.template.settings || {} };
          card = cardFor(entry, c0());
        }
        if (!card && !res.spec) throw new Error('No card could be made from that description. Try wording it differently, or pick one from the gallery.');
        if (!card) {
          const v = validateSpec(res.spec);
          if (!v.ok) throw new Error(`The suggested card is not valid: ${v.errors.join('; ')}`);
          const k = customCard(v.spec);
          entry = { id: `${k.id}-${Date.now().toString(36)}`, custom: k.spec };
          card = cardFor(entry, c0());
        }
        const r = renderCard(card, c0(), ctx, 'summary');
        pending = card;
        preview.replaceChildren(el('strong', { text: card.title }), r.node || '', el('div', { class: 'row' },
          el('button', { class: 'btn primary', type: 'button', onclick: () => { layout.push(entry); saveToLibrary(entry, card.title, description); dlg.close(); paintGrid(); } }, 'Add to my dashboard'),
          el('span', { class: 'small muted', text: 'Saved under "Your cards" so you can add it again later.' })));
        preview.hidden = false; msg.textContent = '';
      } catch (err) { msg.textContent = err.status === 404 || err.status === 405 ? 'Describing a card is not available on this server yet.' : err.status === 503 ? err.message : `Could not make that card: ${err.message}`; }
      go.disabled = false;
    });
    return el('div', { class: 'v2-describe' }, el('h3', { class: 'small muted', text: 'Describe a card' }), ta, el('div', { class: 'row' }, go, msg), preview);
  }

  // Whole-dashboard templates and "describe a dashboard" (cards-a's module). Optional: the page works without it.
  import('./dashboard-templates.js').then(({ dashboardsButton }) => {
    if (destroyed) return;
    const btn = dashboardsButton({ matterId, ctx, defaultLayout: DEFAULT_LAYOUT, getLayout: () => layout, setLayout: (next) => { layout = next; persist(); paintGrid(); }, getCase: c0 });
    if (btn) bar.insertBefore(btn, bar.querySelector('.btn'));
  }).catch(() => { /* module not there yet */ });

  paintGrid();

  return {
    // The live refresh brings a new case model: redraw only the cards that read a key that changed.
    update(changedKeys) {
      graph?.update?.(c0());
      for (const [id, { card, body }] of bodies) {
        if ((card.depends || []).some((k) => changedKeys.has(k))) paintBody({ id }, card, body);
      }
    },
    destroy() { destroyed = true; document.removeEventListener('keydown', onKey); graph?.destroy?.(); },
  };
}
