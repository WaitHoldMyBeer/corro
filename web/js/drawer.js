// Source drawer: opens whatever a SourceRef points at, beside its evidence.
import { api } from './api.js';
import { el, empty, fmtDate, pill } from './util.js';

// The lawyer's own review of a claim (a note, or retired) is shown with the quote. Read once and cached briefly.
let reviewCache = { at: 0, p: null };
function reviewInfo(href) {
  const m = /\/api\/matters\/(\d+)\//.exec(href || '');
  if (!m) return Promise.resolve(null);
  if (!reviewCache.p || Date.now() - reviewCache.at > 30000) {
    reviewCache = { at: Date.now(), p: api(`/api/matters/${m[1]}/review-queue/claims`).catch(() => null) };
  }
  return reviewCache.p;
}


let dlg;
let state = { refs: [], index: 0, title: '' };
// The shell listens here to put the open source in the address bar: { onOpen(ref, page), onClose() }.
const hooks = {};
export function onDrawer(h) { Object.assign(hooks, h); }
// The address of a source: #/c/<case>/source/<kind>/<id>?page=N, built from the ref's own href.
export function sourceLink(ref, page) {
  const m = /\/api\/matters\/([^/]+)\/sources\/([^/?]+)\/([^/?]+)(?:\?(.*))?/.exec(ref?.href || '');
  if (!m) return null;
  const pg = page ?? ref.page ?? (m[4] && new URLSearchParams(m[4]).get('page'));
  return `#/c/${m[1]}/source/${m[2]}/${m[3]}${pg ? `?page=${pg}` : ''}`;
}

function ensure() {
  if (dlg) return dlg;
  dlg = el('dialog', { class: 'drawer', 'aria-label': 'Source' });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => hooks.onClose?.());
  document.body.append(dlg);
  return dlg;
}

function withParams(href, params) {
  const [base, qs = ''] = href.split('?');
  const q = new URLSearchParams(qs);
  for (const [k, v] of Object.entries(params)) {
    if (v == null) q.delete(k); else q.set(k, v);
  }
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

export function chip(ref, title) {
  if (!ref) return null;
  return el('button', {
    class: 'chip', type: 'button', title: title || [ref.label || ref.kind, ref.date ? fmtDate(ref.date) : null].filter(Boolean).join(', ') || 'Open source',
    'data-ai-ref': JSON.stringify({ kind: ref.kind, clio_id: ref.clio_id, label: ref.label, page: ref.page ?? null, date: ref.date ?? null, href: ref.href }),
    onclick: (e) => { e.stopPropagation(); openSources([ref]); },
  }, el('span', { class: 'chip-kind', text: (ref.kind || '').replace('_', ' ') }), el('span', { class: 'chip-label', text: `${ref.label || ref.kind}${ref.page ? `, p. ${ref.page}` : ''}` }),
  ref.date ? el('span', { class: 'chip-date', text: fmtDate(ref.date) }) : null);
}

export function chips(refs) {
  const list = (refs || []).filter(Boolean);
  if (!list.length) return null;
  return el('span', { class: 'chips' }, list.slice(0, 2).map((r) => chip(r)), list.length > 2 ? el('span', { class: 'cs-more', title: `${list.length - 2} more sources`, text: `+${list.length - 2}` }) : null);
}

export function openSources(refs, title = '') {
  const list = (refs || []).filter(Boolean);
  if (!list.length) return;
  state = { refs: list, index: 0, title };
  ensure();
  if (!dlg.open) dlg.showModal();
  load(list[0], null);
}

let currentPage = null;
async function load(ref, page) {
  currentPage = page ?? null;
  hooks.onOpen?.(ref, page);
  const body = el('div', { class: 'drawer-body' }, el('p', { class: 'muted', text: 'Loading the source...' }));
  paint(ref, body);
  try {
    const href = withParams(ref.href, page != null ? { page, quote: null } : (ref.page != null && !/[?&]page=/.test(ref.href) ? { page: ref.page } : {}));
    const detail = await api(href);
    paint(ref, renderDetail(ref, detail, page == null));
  } catch (err) {
    paint(ref, el('div', { class: 'drawer-body' },
      el('p', { class: 'error', text: `Could not open this source: ${err.message}` }),
      ref.quote ? el('blockquote', { text: ref.quote }) : null,
      ref.quote ? el('p', { class: 'muted', text: 'The quote above is what the digest recorded; it could not be checked against the source just now.' }) : null));
  }
}

function copyLink(ref) {
  const b = el('button', { class: 'btn ghost', type: 'button', title: 'Copy a link to exactly this item' }, 'Copy link');
  b.addEventListener('click', async () => {
    const url = `${location.origin}${location.pathname}${location.search}${sourceLink(ref, currentPage) || ''}`;
    try { await navigator.clipboard.writeText(url); b.textContent = 'Link copied'; } catch { window.prompt('Copy this link', url); }
    setTimeout(() => { b.textContent = 'Copy link'; }, 1800);
  });
  return b;
}

function paint(ref, body) {
  const tabs = state.refs.length > 1
    ? el('div', { class: 'drawer-tabs', role: 'tablist' }, state.refs.map((r, i) => el('button', {
      type: 'button', role: 'tab', 'aria-selected': i === state.index ? 'true' : 'false',
      onclick: () => { state.index = i; load(r, null); },
      text: `${r.label || r.kind}${r.page ? `, p. ${r.page}` : ''}`,
    })))
    : null;
  dlg.replaceChildren(...[
    el('header', { class: 'drawer-h' },
      el('div', {}, el('div', { class: 'eyebrow', text: state.title || (ref.kind || '').replace('_', ' ') }), el('h2', { text: ref.label || ref.kind })),
      el('div', { class: 'row' }, sourceLink(ref) ? copyLink(ref) : null, el('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close(), 'aria-label': 'Close' }, 'Close'))),
    tabs, body].filter(Boolean));
}

function highlighted(text, mark) {
  const pre = el('pre', { class: 'source-text' });
  const i = mark ? text.indexOf(mark) : -1;
  if (i < 0) { pre.textContent = text; return pre; }
  pre.append(document.createTextNode(text.slice(0, i)), el('mark', { text: mark }), document.createTextNode(text.slice(i + mark.length)));
  return pre;
}

function renderDetail(ref, d, firstLoad) {
  const r = { ...ref, ...(d.ref || {}) };
  if (!r.quote && ref.quote) { r.quote = ref.quote; r.quote_verified = ref.quote_verified; }
  const meta = el('dl', { class: 'meta' },
    d.date ? [el('dt', { text: ['note', 'communication'].includes(r.kind) ? 'Entered on' : (r.kind === 'document' ? 'Received on' : (r.kind === 'custom_field' ? 'Field last changed on' : 'Date on record')) }), el('dd', { text: fmtDate(d.date) })] : null,
    d.author ? [el('dt', { text: 'From' }), el('dd', { text: d.author })] : null,
    d.parties?.length ? [el('dt', { text: 'Parties' }), el('dd', { text: d.parties.join(', ') })] : null);

  const quoteBlock = (r.quote && firstLoad && r.quote !== d.title && r.quote !== d.text) ? el('div', { class: 'quote' },
    el('blockquote', { text: r.quote }),
    r.quote_verified ? pill('found in the page text', 'ok') : (d.text ? pill('not found in the page text: check the page', 'warn') : pill('read from the page image', 'warn'))) : null;

  let evidence;
  if (d.page_image_href) {
    const page = r.page || 1;
    const img = el('img', { class: 'page-img', src: d.page_image_href, alt: `Page ${page} of ${d.title}` });
    const fallback = el('p', { class: 'muted', hidden: true, text: 'Page image unavailable.' });
    img.addEventListener('error', () => { img.hidden = true; fallback.hidden = false; });
    const nav = el('div', { class: 'pager' },
      el('button', { class: 'btn', type: 'button', disabled: page <= 1, onclick: () => load(state.refs[state.index], page - 1) }, 'Previous'),
      el('span', { text: d.page_count ? `Page ${page} of ${d.page_count}` : `Page ${page}` }),
      el('button', { class: 'btn', type: 'button', disabled: d.page_count != null && page >= d.page_count, onclick: () => load(state.refs[state.index], page + 1) }, 'Next'),
      d.file_href ? el('a', { class: 'btn ghost', href: `${d.file_href}#page=${page}`, target: '_blank', rel: 'noopener' }, 'Open PDF') : null);
    evidence = el('div', {}, nav, el('div', { class: 'page-wrap' }, img, fallback));
  } else if (d.text) {
    evidence = highlighted(d.text, d.highlight || (firstLoad ? r.quote : null));
  } else {
    evidence = empty('This source has no text the app can show.');
  }
  const claimId = /[?&]claim=([^&]+)/.exec(ref.href || '')?.[1];
  const note = el('div', { class: 'review-note' });
  if (claimId) {
    reviewInfo(ref.href).then((info) => {
      if (!info) return;
      const id = decodeURIComponent(claimId);
      const text = info.comments?.[id];
      if (info.retired?.includes(id)) note.append(pill('Retired in review', 'st-stale'), ' ');
      if (text) note.append(el('strong', { text: "Lawyer's note: " }), text);
    });
  }
  const sameAsHeader = (d.title || '') === (ref.label || '');
  return el('div', { class: 'drawer-body' }, sameAsHeader ? null : el('h3', { text: d.title }), meta, quoteBlock, note, evidence);
}
