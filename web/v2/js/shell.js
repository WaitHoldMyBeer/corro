// The v2 shell: left navigation, routing, the case model, live refresh. Tabs are separate modules.
import { api, showMockBanner, isSigningIn } from '../../js/api.js';
import { el, pill, toast, fmtDateTime, empty } from '../../js/util.js';
import { openSources, onDrawer, sourceLink } from '../../js/drawer.js';
import { renderWrite, openWriteWith } from './write.js';
import { renderShare, updateShare, setUnreviewed, resetShare } from '../../js/share.js';
import { setCurrentCase } from './casekey.js';
import { makeCtx, loadCards, registry } from './cards.js';
import { renderDashboard } from './dashboard.js';

showMockBanner();

// null = a group heading; '-' = a rule. Records are the plain lists of what the file holds.
const NAV = [
  ['dashboard', 'Dashboard'], ['graph', 'Graph'], ['documents', 'Documents'], ['notes', 'Notes'], ['communications', 'Communications'],
  ['calendar', 'Calendar'], ['tasks', 'Tasks'], '-',
  ['write', 'Write'], ['share', 'Share'], ['negotiation', 'Negotiation'], ['review', 'Review'], ['assistant', 'Assistant'], '-',
  'Records', ['activities', 'Activities'], ['fields', 'Fields'], ['bills', 'Bills'], ['transactions', 'Transactions'], ['cocounsel', 'Co-counsel'],
];
const RECORD_TABS = new Set(['negotiation', 'review', 'assistant', 'calendar', 'communications', 'notes', 'documents', 'tasks', 'activities', 'fields', 'bills', 'transactions', 'cocounsel']);

const SHOW_ASSISTANT_TAB = true;   // set to false to hide the Assistant entry from the navigation
const SHOW_ALL_CASES = true;   // set to false to hide every "All cases" entry (brand link, link above the name, switcher item)
const OPTIONAL_TABS = [];   // listed only when their module exists, so nothing leads to a stub
const hidden = new Set();
const state = { matterId: null, caseModel: null, sig: {}, ctx: null, view: null, route: null, unreviewed: {}, list: null };
const FIRM_NAV = [['cases', 'Your cases'], ['settings', 'Settings']];
const viewHost = document.getElementById('v2-view');
const navList = document.getElementById('v2-navlist');

// ---------------------------------------------------------------- routing

// Addresses: #/cases (firm level), #/settings (firm level), #/c/<case id>/<tab>?x=y (a case). The older #/tasks style
// still works: it opens that tab on the last case and the address is rewritten to carry the case.
function parseRoute() {
  const [path, qs = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean);
  const params = Object.fromEntries(new URLSearchParams(qs));
  if (parts[0] === 'cases' || parts[0] === 'settings') return { scope: 'firm', name: parts[0], params };
  if (parts[0] === 'c' && parts[1] && parts[2] === 'source' && parts[3] && parts[4]) return { scope: 'case', caseId: idOf(parts[1]), name: 'dashboard', params: {}, source: { kind: parts[3], id: parts[4], page: params.page ? Number(params.page) : null } };
  if (parts[0] === 'c' && parts[1]) return { scope: 'case', caseId: idOf(parts[1]), name: parts[2] || 'dashboard', params };
  if (!parts.length) return { scope: 'land', name: '', params };
  return { scope: 'case', caseId: null, name: parts[0], params };
}
const idOf = (x) => (Number.isFinite(Number(x)) ? Number(x) : x);
const hashFor = (id, name, params) => `#/c/${id}/${name}${params && Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`;
export function navigate(name, params) {
  if (name === 'cases' || name === 'settings') { location.hash = `#/${name}`; return; }
  location.hash = hashFor(state.matterId, name, params);
}

function paintNav() {
  const { name, scope } = state.route || { name: '', scope: 'land' };
  const firm = scope === 'firm' || (scope === 'land' && lastMatter() == null);
  const hadFocus = navList.contains(document.activeElement);
  document.getElementById('v2-brand-link').setAttribute('href', SHOW_ALL_CASES ? '#/cases' : '#/c/' + (state.matterId ?? '') + '/dashboard');
  document.getElementById('v2-matter').hidden = firm;
  navList.replaceChildren(...(firm ? FIRM_NAV.map(([key, label]) => el('li', {}, el('a', { href: `#/${key}`, 'aria-current': key === name ? 'page' : null }, label))) : NAV.map((n) => {
    if (n === '-') return el('li', { class: 'sep', role: 'separator' });
    if (typeof n === 'string') return el('li', { class: 'nav-h', text: n });
    const [key, label] = n;
    if (hidden.has(key) || (key === 'assistant' && !SHOW_ASSISTANT_TAB)) return null;
    const badge = key === 'share' && Object.values(state.unreviewed).reduce((a, b) => a + b, 0);
    return el('li', {}, el('a', { href: hashFor(state.matterId, key), 'aria-current': key === name ? 'page' : null }, label, badge ? pill(String(badge), 'st-contested badge') : null));
  }).filter(Boolean)));
  const foot = document.getElementById('v2-settings');
  foot.hidden = firm;
  foot.setAttribute('aria-current', name === 'settings' ? 'page' : 'false');
  if (hadFocus) navList.querySelector('[aria-current=page]')?.focus();   // the list was rebuilt: keep keyboard focus in it
}

// The address follows the drawer: an open source is #/c/<id>/source/<kind>/<id>?page=N, and closing returns to where it was opened.
let beforeSource = null;
onDrawer({
  onOpen: (ref, page) => {
    const h = sourceLink(ref, page);
    if (!h) return;
    if (!/\/source\//.test(location.hash)) beforeSource = location.hash || hashFor(state.matterId, 'dashboard');
    history.replaceState(null, '', `${location.pathname}${location.search}${h}`);
  },
  onClose: () => {
    if (!/\/source\//.test(location.hash)) return;   // a navigation closed it: leave the new address alone
    history.replaceState(null, '', `${location.pathname}${location.search}${beforeSource || hashFor(state.matterId, 'dashboard')}`);
    beforeSource = null;
  },
});
function openFromAddress(src) {
  if (!src) return;
  const href = `/api/matters/${state.matterId}/sources/${src.kind}/${src.id}`;
  beforeSource = hashFor(state.matterId, 'dashboard');
  openSources([{ kind: src.kind, clio_id: src.id, href, page: src.page, label: src.kind.replace('_', ' ') }]);
}

let showGen = 0;
async function show() {
  const mine = ++showGen;
  state.route ||= parseRoute();
  paintNav();
  document.querySelectorAll('dialog[open]').forEach((d) => d.close());
  state.view?.destroy?.();
  state.view = null;
  const { name, params, scope } = state.route;
  const host = el('div', {});
  if (scope === 'firm') {
    viewHost.replaceChildren(host); viewHost.removeAttribute('aria-busy');
    try { const v = await firmTab(host, name, params); if (mine !== showGen) { v?.destroy?.(); return; } state.view = v; } catch (err) {
      console.error(`view "${name}" failed`, err);
      host.replaceChildren(el('p', { class: 'error', text: `This section could not be drawn (${err.message}). The rest of the app is unaffected.` }));
    }
    return;
  }
  if (!state.caseModel) return;
  viewHost.replaceChildren(host); viewHost.removeAttribute('aria-busy');
  try {
    let v = null;
    if (name === 'dashboard') v = await renderDashboard(host, state);
    else if (name === 'graph') v = await graphTab(host);
    else if (name === 'write' || name === 'share') {
      // Owned by their own tab modules; the inline pages are the fallback if a module is missing.
      const r = await recordTab(host, name, params, true);
      if (r === MISSING) {
        if (name === 'write') { host.append(pageHeader('Write')); const w = el('div', {}); host.append(w); renderWrite(w, state.caseModel, { matterId: state.matterId }); }
        else { const w = el('div', {}); host.append(pageHeader('Share'), w); renderShare(w, state.caseModel, shareCtx()); v = { update: () => updateShare(state.caseModel) }; }
      } else v = r;
    }
    else if (RECORD_TABS.has(name)) v = await recordTab(host, name, params);
    else host.append(empty('There is nothing at this address.'));
    if (mine !== showGen) { v?.destroy?.(); return; }   // a newer show() took over while this one was loading
    state.view = v;
  } catch (err) {
    console.error(`view "${name}" failed`, err);
    host.replaceChildren(el('p', { class: 'error', text: `This section could not be drawn (${err.message}). The rest of the app is unaffected.` }));
  }
}

// Firm level: outside any case. Your cases is cards-a's module; Settings is its own module, with the inline page as the fallback.
async function firmTab(host, name, params) {
  const ctx = firmCtx();
  if (name === 'cases') {
    await refreshList();
    let m;
    try { m = await import('../tabs/cases.js'); } catch (err) { host.append(pageHeader('Your cases'), empty('This section is not available yet.')); return null; }
    return (await (m.default || m.mount)(host, null, ctx, params)) || null;
  }
  const r = await recordTab(host, 'settings', params, true, ctx);
  if (r === MISSING) { host.append(...settingsPage()); return null; }
  return r;
}

const pageHeader = (title, sub) => el('div', { class: 'v2-page-h' }, el('h1', { text: title }), sub ? el('span', { class: 'muted', text: sub }) : null);

async function graphTab(host) {
  host.append(pageHeader('Graph'));
  const box = el('div', { class: 'v2-hero has-graph' });
  host.append(box);
  try {
    const m = await import('./graph/index.js');
    return m.mountGraph(box, { matterId: state.matterId, api, openSource: (ref, title) => openSources([ref], title), compact: false }) || null;
  } catch { box.replaceChildren(el('div', { class: 'v2-hero-empty', text: 'The graph is being built.' })); return null; }
}

const MISSING = Symbol('missing');

// Tab modules: default export (host, caseModel, ctx, params) => void | { update?(changedKeys, caseModel), destroy?() }, sync or async.
async function recordTab(host, name, params, quietMissing = false, ctx = state.ctx) {
  let m;
  try { m = await import(`../tabs/${name}.js`); } catch (err) {
    if (/Failed to fetch dynamically imported module|Cannot find module|404/.test(err.message)) {
      if (quietMissing) return MISSING;
      host.replaceChildren(pageHeader(NAV.find((n) => n[0] === name)[1]), empty('This section is not available yet.'));
      return null;
    }
    throw err;
  }
  const fn = m.default || m.mount;
  const r = fn(host, state.caseModel, ctx, params);
  return (await r) || null;
}

function shareCtx() {
  return { matterId: state.matterId, refreshBadges, reload: async () => { await refresh(true); return state.caseModel; } };
}

// ---------------------------------------------------------------- settings: the only place that names the source system

function settingsPage() {
  const line = el('p', { class: 'small muted', role: 'status' });
  const sync = el('button', { class: 'btn primary', type: 'button' }, 'Sync now');
  sync.addEventListener('click', async () => {
    sync.disabled = true; line.textContent = 'Reading the source system...';
    try {
      const r = await api(`/api/matters/${state.matterId ?? lastMatter()}/sync`, { method: 'POST' });
      line.textContent = `${r.changed} changed item${r.changed === 1 ? '' : 's'}, ${r.requests} read${r.requests === 1 ? '' : 's'} (read-only).${(r.warnings || []).length ? ` ${r.warnings.join(' ')}` : ''}`;
      await refresh(true);
    } catch (err) { line.textContent = `Sync failed: ${err.message}`; }
    sync.disabled = false;
  });
  const digest = el('button', { class: 'btn', type: 'button' }, 'Analyse changed records');
  digest.addEventListener('click', async () => {
    try { await api(`/api/matters/${state.matterId ?? lastMatter()}/digest`, { method: 'POST' }); toast('Analysis started. Cards fill in as it runs.'); } catch (err) { toast(`Not started: ${err.message}`, 'error'); }
  });
  const d = state.caseModel?.meta?.digest || {};
  return [pageHeader('Settings'),
    el('section', { class: 'card' }, el('h2', { text: 'Import' }),
      el('p', { class: 'muted', text: 'The matter is imported from Clio, read-only: nothing here ever writes back.' }),
      el('div', { class: 'row' }, sync, el('a', { class: 'btn', href: '/oauth/start' }, 'Import from Clio'), digest), line,
      el('p', { class: 'small muted', text: `Last import: ${fmtDateTime(state.caseModel?.meta?.synced_at) || 'never'}. Analysis cost so far: $${(d.cost_usd_total ?? 0).toFixed(2)}.` })),
    el('section', { class: 'card' }, el('h2', { text: 'Cards' }),
      el('p', { class: 'small muted', text: `${registry.cards.size} card${registry.cards.size === 1 ? '' : 's'} available.` }),
      registry.problems.length ? el('ul', { class: 'small error' }, registry.problems.map((p) => el('li', { text: p }))) : null)];
}

// ---------------------------------------------------------------- data

const keyed = (c) => Object.fromEntries(Object.keys(c).map((k) => [k, JSON.stringify(c[k])]));

// The server keeps the "since you last opened" marker stable within a visit, so no ?since= is passed: its stored marker is the default.
const fetchCase = (id = state.matterId) => api(`/api/matters/${id}/case`);

// Every read below checks the case is still the open one when it returns, so a slow answer for A never lands in B.
async function refresh(force = false) {
  const id = state.matterId;
  const next = await fetchCase(id);
  if (id !== state.matterId) return new Set();
  const now = keyed(next);
  const changed = new Set(Object.keys(now).filter((k) => now[k] !== state.sig[k]));
  state.sig = now;
  state.caseModel = next;
  state.ctx.caseModel = next;
  paintMatter(); paintProgress();
  if (force) return changed;
  if (changed.size) { state.view?.update?.(changed, next); }
  return changed;
}

async function refreshBadges() {
  const id = state.matterId;
  if (id == null) return;
  try {
    const u = (await api(`/api/matters/${id}/incoming-checks/unreviewed`)) || {};
    if (id !== state.matterId) return;
    state.unreviewed = u;
    setUnreviewed(state.unreviewed); paintNav();
  } catch { /* no such route: no badge */ }
}

const caseName = (m) => m.client_name || m.description || m.display_number || 'Case';
async function refreshList() {
  try { state.list = await api('/api/matters'); } catch { /* the last list stays */ }
  return state.list;
}

function paintMatter() {
  const c = state.caseModel;
  const host = document.getElementById('v2-matter');
  if (!c) { host.replaceChildren(); return; }
  const name = c.matter?.client?.name || c.matter?.description || 'Matter';
  const trigger = el('button', { class: 'v2-switch', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', title: 'Switch case' },
    el('span', { class: 'v2-switch-t' }, el('strong', { text: name }), el('span', { class: 'v2-switch-n', text: c.matter?.display_number || '' })), el('span', { class: 'v2-switch-c', 'aria-hidden': 'true', text: '▾' }));
  trigger.addEventListener('click', () => openSwitcher(trigger));
  host.replaceChildren(SHOW_ALL_CASES ? el('a', { class: 'v2-allcases small', href: '#/cases' }, '‹ All cases') : null, trigger);
  providerMenu();
  document.getElementById('v2-status').textContent = c.meta?.synced_at ? `Imported ${fmtDateTime(c.meta.synced_at)}` : '';
}

// The compact switcher: the other cases by name (a search box above eight), All cases, New case.
let menu = null;
function closeSwitcher(focus = false) {
  if (!menu) return;
  const { box, trigger, off } = menu; menu = null;
  off(); box.remove(); trigger.setAttribute('aria-expanded', 'false');
  if (focus) trigger.focus();
}
async function openSwitcher(trigger) {
  if (menu) { closeSwitcher(true); return; }
  trigger.setAttribute('aria-expanded', 'true');
  const box = el('div', { class: 'v2-switch-menu', role: 'menu' });
  trigger.parentElement.append(box);
  const onKey = (e) => { if (e.key === 'Escape') closeSwitcher(true); };
  const onDown = (e) => { if (!box.contains(e.target) && !trigger.contains(e.target)) closeSwitcher(); };
  document.addEventListener('keydown', onKey); document.addEventListener('mousedown', onDown);
  menu = { box, trigger, off: () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown); } };
  const paint = (q = '') => {
    const others = (state.list?.items || []).filter((m) => !m.archived && m.id !== state.matterId);
    const shown = others.filter((m) => !q || `${caseName(m)} ${m.display_number || ''}`.toLowerCase().includes(q.toLowerCase()));
    const rows = shown.map((m) => el('button', { class: 'v2-switch-i', type: 'button', role: 'menuitem', onclick: () => { closeSwitcher(); openCase(m.id); } },
      el('span', { text: caseName(m) }), m.display_number ? el('span', { class: 'muted small', text: m.display_number }) : null));
    const list = box.querySelector('.v2-switch-l');
    list.replaceChildren(...(rows.length ? rows : [el('span', { class: 'muted small pad', text: others.length ? 'No case matches.' : 'No other cases yet.' })]));
    return others.length;
  };
  box.append(el('div', { class: 'v2-switch-l' }));
  const n = paint();
  if (n > 8) {
    const q = el('input', { type: 'search', class: 'v2-switch-q', placeholder: 'Find a case', 'aria-label': 'Find a case' });
    q.addEventListener('input', () => paint(q.value.trim()));
    box.prepend(q); q.focus();
  }
  box.append(el('div', { class: 'v2-switch-sep', role: 'separator' }),
    SHOW_ALL_CASES ? el('a', { class: 'v2-switch-i', role: 'menuitem', href: '#/cases', onclick: () => closeSwitcher() }, 'All cases') : null,
    el('button', { class: 'v2-switch-i', type: 'button', role: 'menuitem', onclick: () => { closeSwitcher(); openNewCase(); } }, 'New case'));
  if (!(n > 8)) box.querySelector('.v2-switch-i, a')?.focus?.();
  await refreshList();
  if (menu && menu.box === box) { const q = box.querySelector('.v2-switch-q'); paint(q?.value.trim() || ''); }
}

// Provider view: the case's providers; a share that was sent opens its live link in a new tab, otherwise the read-only preview in Share.
function providerMenu() {
  const host = document.getElementById('v2-actions');
  let btn = document.getElementById('v2-provider-btn');
  const c = state.caseModel;
  if (state.route?.scope !== 'case' || !c?.providers?.length) { btn?.remove(); return; }
  if (!btn) {
    btn = el('button', { id: 'v2-provider-btn', class: 'btn', type: 'button', 'aria-haspopup': 'menu', title: 'See a provider\'s page as the provider sees it' }, 'Provider view');
    btn.addEventListener('click', () => {
      const old = document.getElementById('v2-provider-menu');
      if (old) { old.remove(); return; }
      const live = (p) => (p.shares || []).filter((x) => x.link_href && !x.revoked_at && x.state !== 'revoked').slice(-1)[0];
      const note = el('p', { class: 'small muted pad', role: 'status', text: 'Choose a provider.' });
      const box = el('div', { id: 'v2-provider-menu', class: 'v2-switch-menu', role: 'menu', style: 'right:0;left:auto;top:100%;min-width:260px' }, ...(state.caseModel.providers || []).map((p) => {
        const sh = live(p);
        return el('button', { class: 'v2-switch-i', type: 'button', role: 'menuitem', onclick: () => {
          if (sh) { window.open(sh.link_href, '_blank', 'noopener'); note.textContent = `Opened the live link sent to ${p.contact.name}.`; }
          else { note.textContent = `No link sent to ${p.contact.name} yet: showing the read-only preview in Share.`; box.remove(); navigate('share'); toast(note.textContent); }
        } }, el('span', { text: p.contact.name }), el('span', { class: 'muted small', text: sh ? 'live link' : 'read-only preview' }));
      }), note);
      btn.parentElement.style.position = 'relative'; btn.after(box);
      const off = (e) => { if (!box.contains(e.target) && e.target !== btn) { box.remove(); document.removeEventListener('mousedown', off); } };
      document.addEventListener('mousedown', off);
    });
    host.prepend(btn);
  }
}

function openNewCase() {
  import('./newcase.js').then((m) => m.openNewCase({ onCreated: (id) => (id != null ? openCase(id) : navigate('cases')) }))
    .catch(() => toast('Creating a case is not available here yet.', 'error'));
}

function paintProgress() {
  const d = state.caseModel?.meta?.digest || {};
  const p = document.getElementById('v2-progress');
  const live = d.state === 'running' || d.state === 'partial';
  p.hidden = !live;
  if (!live) return;
  const pages = d.pages_total > 0, done = pages ? d.pages_digested : d.items_digested, total = pages ? d.pages_total : d.items_total;
  p.replaceChildren(el('span', { text: d.state === 'running' ? `Reading documents: ${done} of ${total} ${pages ? 'pages' : 'items'}` : `Analysis partial: ${done} of ${total}` }),
    ...(total > 0 ? [el('span', { class: 'bar' }, el('span', { class: 'fill', style: `transform:scaleX(${Math.min(1, done / total)});transform-origin:left` }))] : []));
}

// ---------------------------------------------------------------- cases: open, switch, tear down

let timer = null;
let tickGen = 0;
async function tick(gen = tickGen) {
  if (gen !== tickGen) return;
  if (!document.hidden && state.caseModel && state.route?.scope === 'case' && !isSigningIn()) {
    try { await refresh(); refreshBadges(); } catch { /* transient */ }
  }
  if (gen === tickGen) timer = setTimeout(() => tick(gen), state.caseModel?.meta?.digest?.state === 'running' ? 3000 : 6000);
}
const startTick = () => { clearTimeout(timer); const g = ++tickGen; timer = setTimeout(() => tick(g), 3000); };

const lastKey = 'v2:lastMatter';
const lastMatter = () => { try { return idOf(localStorage.getItem(lastKey)) || null; } catch { return null; } };
const remember = (id) => { try { localStorage.setItem(lastKey, String(id)); } catch { /* storage may be blocked */ } };
const forgetLast = () => { try { localStorage.removeItem(lastKey); } catch { /* storage may be blocked */ } };

const caseCtx = (id) => {
  const ctx = makeCtx({
    matterId: id,
    openTab: (name, params) => navigate(name, params),
    openWrite: (text, audience) => openWriteWith(text, audience),
    toast,
    reload: async () => { await refresh(true); state.view?.update?.(new Set(Object.keys(state.caseModel)), state.caseModel); return state.caseModel; },
  });
  ctx.refreshBadges = refreshBadges;
  ctx.caseModel = null;
  ctx.openCase = openCase; ctx.openNewCase = openNewCase; ctx.selectMatter = openCase;
  ctx.linkTo = (ref, page) => { const h = sourceLink(ref, page); return h ? `${location.origin}${location.pathname}${location.search}${h}` : null; };
  return ctx;
};
function firmCtx() {
  const ctx = makeCtx({ matterId: state.matterId ?? lastMatter(), openTab: (name, params) => navigate(name, params), openWrite: () => {}, toast, reload: async () => { await refreshList(); } });
  ctx.openCase = openCase; ctx.openNewCase = openNewCase; ctx.selectMatter = openCase;
  return ctx;
}

// Everything that belongs to the case being left: timers, the view (its graph and listeners), open dialogs and the source drawer,
// the assistant conversation, the Share module's remembered rows and the badge counts.
function teardown() {
  clearTimeout(timer); timer = null; tickGen += 1;
  closeSwitcher();
  document.querySelectorAll('dialog[open]').forEach((d) => d.close());
  state.view?.destroy?.(); state.view = null;
  resetShare();
  state.unreviewed = {}; state.sig = {}; state.caseModel = null;
  document.getElementById('v2-progress').hidden = true;
  import('./assistant/store.js').then((st) => { st.stop?.(); st.newConversation?.(); st.clearContext?.(); }).catch(() => {});
  window.dispatchEvent(new CustomEvent('case:closed'));
}

// Open a case on its Dashboard (or on `tab`): the address carries the case, so a reload or a shared link opens the same one.
export function openCase(id, tab = 'dashboard') {
  if (id == null) return;
  const next = hashFor(idOf(id), tab);
  if (location.hash === next) { go(); return; }
  location.hash = next;
}

let early = null;     // { id, p }: the last case's data, asked for while the list loads
let loadSeq = 0;
async function loadCase(id, route) {
  const seq = ++loadSeq;
  teardown();
  state.matterId = id; state.route = route;
  setCurrentCase(id); remember(id);
  paintNav(); paintMatter(); paintProgress();
  viewHost.replaceChildren(el('p', { class: 'muted pad', text: 'Opening the case...' })); viewHost.setAttribute('aria-busy', 'true');
  state.ctx = caseCtx(id);
  let model = null;
  if (early && early.id === id) model = await early.p;
  early = null;
  try { model ||= await fetchCase(id); } catch (err) {
    if (seq !== loadSeq) return;
    if (err.status === 404) forgetLast();
    viewHost.removeAttribute('aria-busy');
    viewHost.replaceChildren(el('div', { class: 'pad' }, el('p', { class: 'error', text: err.status === 404 ? 'That case is not in this product.' : `Could not open the case (${err.status ? `HTTP ${err.status}: ` : ''}${err.message}).` }),
      el('p', {}, el('a', { class: 'btn', href: '#/cases' }, 'Your cases'), ' ', el('button', { class: 'btn', type: 'button', onclick: () => loadCase(id, route) }, 'Try again'))));
    return;
  }
  if (seq !== loadSeq) return;
  state.caseModel = model;
  state.sig = keyed(model); state.ctx.caseModel = model;
  paintMatter(); paintProgress();
  api(`/api/matters/${id}/opened`, { method: 'POST' }).catch(() => { /* the marker is optional */ });
  await show();
  if (seq !== loadSeq) return;
  openFromAddress(route.source);
  refreshBadges(); startTick();
}

// One case and no stored choice: open it. A stored last case that still exists: open it. Otherwise Your cases.
function landingHash() {
  const items = (state.list?.items || []).filter((m) => !m.archived);
  const last = lastMatter();
  if (last != null && items.some((m) => m.id === last)) return hashFor(last, 'dashboard');
  if (items.length === 1) return hashFor(items[0].id, 'dashboard');
  return '#/cases';
}

async function go() {
  closeSwitcher();
  let r = parseRoute();
  if (r.scope === 'land') { history.replaceState(null, '', landingHash()); r = parseRoute(); }
  if (r.scope === 'case') {
    if (r.caseId == null) {
      r.caseId = state.matterId ?? lastMatter();
      if (r.caseId == null) { history.replaceState(null, '', landingHash()); return go(); }
      history.replaceState(null, '', hashFor(r.caseId, r.name, r.params));   // an older #/tasks address now carries its case
    }
    if (r.caseId !== state.matterId || !state.caseModel) { await loadCase(r.caseId, r); return; }
    const wasPaused = state.route?.scope !== 'case';
    state.route = r;
    await show();
    openFromAddress(r.source);
    if (wasPaused) { refresh().catch(() => {}); refreshBadges(); startTick(); }
    return;
  }
  state.route = r;
  clearTimeout(timer); tickGen += 1;
  await show();
}

async function boot() {
  // The matter list can be slow, so ask for the case the address (or the last visit) names at the same time.
  const first = parseRoute();
  const guess = first.scope === 'case' && first.caseId != null ? first.caseId : (first.scope === 'land' || first.scope === 'case') ? lastMatter() : null;
  if (guess != null) early = { id: guess, p: fetchCase(guess).catch(() => null) };
  try { state.list = await api('/api/matters'); } catch (err) { viewHost.replaceChildren(el('p', { class: 'error pad', text: `Could not reach the server: ${err.message}` })); setTimeout(boot, 4000); return; }
  await loadCards();
  import('./assistant/index.js').then((m) => m.mountAssistant({ getMatterId: () => state.matterId })).catch((err) => console.error('assistant', err));
  window.addEventListener('hashchange', go);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.route?.scope === 'case') { startTick(); } });
  await go();
}

state.route = parseRoute();
paintNav();
boot();

// Optional modules that mount themselves into the shell. Each is a dynamic import so a missing or broken one never stops /v2/.
import('./aiifier/index.js').then((m) => m.mountAiifier?.()).catch(() => {});
