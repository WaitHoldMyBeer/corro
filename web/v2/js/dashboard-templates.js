// Dashboard templates: named layouts of existing cards, "describe a dashboard", save-current and reset.
// Applying a layout goes through the one setLayout function the dashboard hands in, and always leaves an Undo.
// A layout entry is { id } | { id: '<base>:<stamp>', base, settings } | { id, custom: spec }, as dashboard.js stores them.
import { el } from '../../js/util.js';
import { api } from '../../js/api.js';
import { loadCards, registry, validateSpec, customCard } from './cards.js';

// Layouts are card ids only (and template settings by key); nothing here is about a particular matter.
// A card that is not registered is skipped when the layout is applied.
const T = (base, settings) => ({ base, settings });
export const TEMPLATES = [
  { key: 'first-look', name: 'First look', about: 'The default: where the case stands, the money, what to do next and what is overdue.', layout: null /* = defaultLayout */ },
  { key: 'treatment', name: 'Treatment and providers', about: 'Who is treating, who has been heard from and what each office is waiting on.',
    layout: ['case-glance', 'providers', 'last-contact', 'medical-bills', T('progress', { kind: 'asks' }), T('checklist', { bucket: 'waiting' }), 'for-review', 'important-documents'] },
  { key: 'demand', name: 'Building the demand', about: 'Medical charges, wage loss, liens and the documents behind each figure.',
    layout: ['money', 'medical-bills', T('breakdown', {}), 'liens-costs', 'key-facts', 'important-documents', T('timeline', {}), 'firm-spend'] },
  { key: 'negotiation', name: 'Negotiation', about: 'Value against coverage, what is held back and what the client keeps.',
    layout: ['money', 'coverage', 'what-is-left', T('compare', { a: 'fact:case_value', b: 'fact:coverage' }), 'liens-costs', 'next-moves', 'last-contact'] },
  { key: 'litigation', name: 'Litigation and deadlines', about: 'Deadlines, what is overdue and coming up, and the record in date order.',
    layout: ['deadlines', 'overdue', 'this-week', T('checklist', { bucket: 'coming' }), 'waiting', T('timeline', {}), 'for-review', 'important-documents'] },
  { key: 'money', name: 'Money', about: 'The value river and every layer behind it: coverage, bills, liens, costs, spend.',
    layout: ['money', 'coverage', 'medical-bills', 'liens-costs', 'firm-spend', 'what-is-left'] },
  { key: 'intake', name: 'Intake', about: 'A case just opened: the basics, the first moves and what is still missing.',
    layout: ['case-glance', 'key-facts', 'next-moves', T('checklist', { bucket: 'overdue' }), T('progress', { kind: 'tasks' }), 'providers', 'important-documents'] },
];

const LS_SAVED = 'v2:dash-templates';
const ls = {
  get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage may be blocked */ } },
};
const stamp = () => Date.now().toString(36);

let cssLinked = false;
function linkCss() {
  if (cssLinked) return;
  cssLinked = true;
  document.head.append(el('link', { rel: 'stylesheet', href: new URL('../css/dashboard-templates.css', import.meta.url).href }));
}

// One shape for everything: a string id, { id }, the server's { id, spec: { base, settings } | spec }, or the layout's own forms.
function norm(x) {
  const e = typeof x === 'string' ? { id: x } : x;
  if (e.spec?.base) return { ...e, base: e.spec.base, settings: e.spec.settings || {}, spec: undefined };
  return e;
}

// Turn a stored or proposed list into real layout entries; drop what is not registered. Returns { layout, dropped }.
export function resolveLayout(list) {
  const out = [];
  let dropped = 0;
  list.forEach((x, i) => {
    const e = norm(x);
    if (e.spec || e.custom) {
      const v = validateSpec(e.spec || e.custom);
      if (!v.ok) { dropped += 1; return; }
      const k = customCard(v.spec);
      out.push({ id: `${k.id}-${stamp()}${i}`, custom: k.spec });
    } else if (e.base) {
      if (!registry.cards.get(e.base)?.template) { dropped += 1; return; }
      out.push({ id: `${e.base}:${stamp()}${i}`, base: e.base, settings: e.settings || {} });
    } else if (registry.cards.has(e.id)) {
      if (!out.some((o) => o.id === e.id)) out.push({ id: e.id });
    } else dropped += 1;
  });
  return { layout: out, dropped };
}

// A static thumbnail: one box per card, spanning the columns the shell would give it. Not live.
function preview(entries) {
  const grid = el('div', { class: 'dt-prev', 'aria-hidden': 'true' });
  for (const x of entries) {
    const e = norm(x);
    const card = registry.cards.get(e.base || e.id);
    if (!card && !(e.spec || e.custom)) continue;
    grid.append(el('span', { class: `dt-box ${card?.size || 'm'}`, title: card?.title || e.title || '' }));
  }
  return grid;
}

// Named dashboards live on the server when it holds them (per matter), else in this browser.
const toStored = (layout) => layout.map((e, i) => (e.base ? { id: `${e.base}:${i}`, spec: { base: e.base, settings: e.settings || {} } }
  : e.spec || e.custom ? { id: `custom-${i}`, spec: e.spec || e.custom } : { id: e.id }));

function makeStore(matterId) {
  let mode = 'local';
  async function list() {
    try {
      const res = await api(`/api/matters/${matterId}/dashboard`);
      if (Array.isArray(res?.dashboards)) { mode = 'server'; return res.dashboards.map((d) => ({ key: d.id, name: d.title, layout: d.cards })); }
    } catch { /* no route, or not allowed: use this browser */ }
    mode = 'local';
    return (ls.get(LS_SAVED) || []).map((s) => ({ key: s.name, name: s.name, layout: s.layout }));
  }
  async function write(change) {
    if (mode === 'server') {
      // read before write, and send only the list this changes
      const res = await api(`/api/matters/${matterId}/dashboard`);
      const next = change((res.dashboards || []).map((d) => ({ key: d.id, name: d.title, layout: d.cards })));
      await api(`/api/matters/${matterId}/dashboard`, { method: 'PUT', body: { dashboards: next.map((d) => ({ id: d.key, title: d.name, cards: d.layout })) } });
      return;
    }
    ls.set(LS_SAVED, change((ls.get(LS_SAVED) || []).map((s) => ({ key: s.name, name: s.name, layout: s.layout }))).map((d) => ({ name: d.name, layout: d.layout })));
  }
  return {
    list,
    add: (name, layout) => write((all) => [...all.filter((d) => d.name !== name), { key: mode === 'server' ? `d-${stamp()}` : name, name, layout: toStored(layout) }]),
    remove: (key) => write((all) => all.filter((d) => d.key !== key)),
  };
}

export function dashboardsButton({ matterId, ctx, defaultLayout, getLayout, setLayout }) {
  linkCss();
  let snack = null;
  const store = makeStore(matterId);

  function apply(entries, label) {
    const { layout, dropped } = resolveLayout(entries);
    if (!layout.length) { ctx.toast?.('None of those cards are available, so nothing was changed.', 'error'); return false; }
    const previous = JSON.parse(JSON.stringify(getLayout()));
    setLayout(layout);
    showUndo(previous, `${label}${dropped ? ` (${dropped} card${dropped === 1 ? '' : 's'} not available)` : ''}`);
    return true;
  }

  function showUndo(previous, label) {
    snack?.remove();
    const bar = el('div', { class: 'dt-snack', role: 'status' },
      el('span', { text: `Dashboard changed: ${label}.` }),
      el('button', { class: 'btn small', type: 'button', onclick: () => { setLayout(previous); bar.remove(); snack = null; } }, 'Undo'),
      el('button', { class: 'btn small quiet', type: 'button', 'aria-label': 'Dismiss', onclick: () => { bar.remove(); snack = null; } }, 'Dismiss'));
    snack = bar;
    document.body.append(bar);
    setTimeout(() => { if (snack === bar) { bar.remove(); snack = null; } }, 15000);
  }

  async function open() {
    await loadCards();
    const dlg = el('dialog', { class: 'v2-dlg dt-dlg', 'aria-label': 'Dashboards' });
    const close = () => { dlg.close(); dlg.remove(); };
    dlg.addEventListener('cancel', () => dlg.remove());
    const body = el('div', { class: 'v2-dlg-b' });
    dlg.append(el('div', { class: 'v2-dlg-h' }, el('h2', { text: 'Dashboards' }), el('button', { class: 'btn quiet', type: 'button', onclick: close }, 'Close')), body);

    const tile = (name, about, entries, actions) => el('div', { class: 'dt-tile' },
      preview(entries),
      el('div', { class: 'dt-tile-t' }, el('strong', { text: name }), el('span', { class: 'dt-n', text: `${entries.length} cards` })),
      el('p', { class: 'dt-about', text: about }),
      el('div', { class: 'dt-actions' }, actions));

    async function paint() {
      let saved = [];
      try { saved = await store.list(); } catch { /* shown as nothing saved */ }
      const ready = el('div', { class: 'dt-grid' }, TEMPLATES.map((t) => {
        const entries = t.layout || defaultLayout;
        return tile(t.name, t.about, entries, el('button', { class: 'btn small', type: 'button', onclick: () => { if (apply(entries, t.name)) close(); } }, 'Use this dashboard'));
      }));
      const mine = saved.length
        ? el('div', { class: 'dt-grid' }, saved.map((s) => tile(s.name, 'Saved from a dashboard you arranged.', s.layout, [
          el('button', { class: 'btn small', type: 'button', onclick: () => { if (apply(s.layout, s.name)) close(); } }, 'Use this dashboard'),
          el('button', { class: 'btn small quiet', type: 'button', onclick: async () => { try { await store.remove(s.key); } catch (err) { ctx.toast?.(`Not deleted: ${err.message}`, 'error'); } paint(); } }, 'Delete'),
        ])))
        : el('p', { class: 'v2-empty', text: 'Nothing saved yet. Arrange your dashboard, then save it here.' });

      const nameIn = el('input', { type: 'text', placeholder: 'Name for this dashboard', 'aria-label': 'Template name', maxlength: 60 });
      const saveBtn = el('button', { class: 'btn small', type: 'button', text: 'Save current as template' });
      saveBtn.addEventListener('click', async () => {
        const name = nameIn.value.trim();
        if (!name) { nameIn.focus(); return; }
        saveBtn.disabled = true;
        try { await store.add(name, getLayout()); nameIn.value = ''; } catch (err) { ctx.toast?.(`Not saved: ${err.message}`, 'error'); }
        saveBtn.disabled = false;
        paint();
      });
      const reset = el('button', { class: 'btn small quiet', type: 'button', onclick: () => { if (apply(defaultLayout, 'Default')) close(); } }, 'Reset to default');

      body.replaceChildren(
        describeBox(),
        el('section', { class: 'v2-gal-group' }, el('h3', { text: 'Ready-made dashboards' }), ready),
        el('section', { class: 'v2-gal-group' }, el('h3', { text: 'Yours' }), mine,
          el('div', { class: 'dt-save' }, nameIn, saveBtn, el('span', { class: 'grow' }), reset)));
    }

    function describeBox() {
      const ta = el('textarea', { rows: 2, placeholder: 'Describe the dashboard you want, for example: what I need before a mediation', 'aria-label': 'Describe a dashboard' });
      const go = el('button', { class: 'btn primary', type: 'button', text: 'Design it' });
      const out = el('div', { class: 'dt-out', 'aria-live': 'polite' });
      go.addEventListener('click', async () => {
        const prompt = ta.value.trim();
        if (!prompt) { ta.focus(); return; }
        go.disabled = true;
        out.replaceChildren(el('span', { class: 'v2-skel short' }));
        try {
          const res = await api(`/api/matters/${matterId}/cards/dashboard`, { method: 'POST', body: { prompt } });
          if (res.error) throw new Error(res.error);
          const proposed = (res.cards || res.layout || []);
          const resolved = resolveLayout(proposed);
          const layout = resolved.layout;
          const dropped = resolved.dropped + (Number(res.skipped) || 0);
          if (!layout.length) throw new Error('No dashboard could be made from that description. Try wording it differently, or pick one below.');
          const reasons = proposed.map((x) => x.reason).filter(Boolean);
          out.replaceChildren(el('div', { class: 'dt-prop' },
            el('strong', { text: res.title || 'Proposed dashboard' }),
            preview(proposed),
            el('ul', { class: 'v2-list' }, proposed.map((x, i) => {
              const e = norm(x);
              const title = registry.cards.get(e.base || e.id)?.title || (e.spec || e.custom ? x.title : null);
              return title ? el('li', { class: 'v2-row' }, el('span', { class: 'v2-row-title', text: x.title && e.base ? `${registry.cards.get(e.base)?.title}: ${x.title}` : title }), el('span', { class: 'v2-row-meta', text: x.reason || '' })) : null;
            })),
            reasons.length ? null : el('p', { class: 'v2-empty', text: 'No reasons were given for these cards.' }),
            dropped ? el('p', { class: 'v2-empty', text: `${dropped} suggested card${dropped === 1 ? '' : 's'} could not be used and ${dropped === 1 ? 'was' : 'were'} left out.` }) : null,
            res.note ? el('p', { class: 'v2-empty', text: res.note }) : null,
            el('div', { class: 'dt-actions' },
              el('button', { class: 'btn primary', type: 'button', onclick: () => { if (apply(proposed, res.title || 'Designed dashboard')) close(); } }, 'Use this dashboard'),
              el('button', { class: 'btn quiet', type: 'button', onclick: () => out.replaceChildren() }, 'Discard'))));
        } catch (err) {
          out.replaceChildren(el('p', { class: 'error', text: `Could not design a dashboard: ${err.message}` }));
        }
        go.disabled = false;
      });
      return el('section', { class: 'v2-describe' }, el('h3', { text: 'Describe a dashboard' }), ta, el('div', { class: 'dt-actions' }, go), out);
    }

    document.body.append(dlg);
    dlg.showModal();
    paint();
  }

  return el('button', { class: 'btn', type: 'button', onclick: open }, 'Dashboards');
}
