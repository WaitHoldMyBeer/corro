// Every file the assistant has made on this matter, newest first. A row opens the PDF viewer
// (through `onOpen`) and has its own Download.
//
//   filesList(el, { matterId, onOpen, event? }) -> { refresh(), destroy() }
//
// It refetches when the window hears `event` (a turn created a document) and when refresh() is called.
// A row's "⋯" asks, inside the row, whether to delete the file; Delete removes the assistant's saved copy only.
import { el, fmtDateTime, toast } from '../../../js/util.js';
import { loadCss } from '../../tabs/_css.js';
import { MOCK_ASSISTANT, listDocuments } from './client.js';
import { api, ApiError } from '../../../js/api.js';
import { describe, kindWords, learnKinds, downloadDocument, closeDocument, VIEWER_EVENT } from './viewer.js';

export const CREATED_EVENT = 'assistant:document-created';
const SEARCH_FROM = 9;                              // search appears when there are more than eight
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const dot = (...parts) => parts.filter(Boolean).join(' · ');

// Mock mode: placeholder rows on neutral dates, plus whatever the mock turns of this visit made.
const made = [], removed = new Set();
function mockRows() { return mockAll().filter((r) => !removed.has(String(r.id))); }
function mockAll() {
  if (new URLSearchParams(location.search).get('mock') === 'empty') return [...made];
  const kinds = ['medical_chronology', 'records_summary', 'damages_summary'];
  const base = Date.UTC(2000, 0, 3, 9);
  return [...made, ...Array.from({ length: 10 }, (_, i) => ({
    id: `mock-doc-${i + 1}`, kind: kinds[i % 3], title: `Placeholder document ${i + 1}`, created_at: new Date(base + i * 26 * 36e5).toISOString(),
    entries: 6 + i * 5, citations: 3 + i * 2, conversation_id: `mock-conversation-${(i % 3) + 1}`,
  }))];
}

export function filesList(host, { matterId, onOpen, event = CREATED_EVENT } = {}) {
  loadCss('assistant-viewer.css');
  let rows = [], query = '', openId = null, asking = null, seq = 0, gone = false;
  const search = el('input', { type: 'search', class: 'av-search', placeholder: 'Search files', 'aria-label': 'Search files by title', hidden: true });
  const list = el('ul', { class: 'av-files' });
  const note = el('div', { class: 'av-files-note' });
  host.replaceChildren(el('div', { class: 'av-files-wrap' }, search, note, list));
  search.addEventListener('input', () => { query = search.value.trim().toLowerCase(); paint(); });

  // Removes the assistant's saved copy (our own store). The matter's documents are never touched.
  async function remove(d, retried = false) {
    if (MOCK_ASSISTANT) { removed.add(d.id); return; }
    const res = await fetch(`/api/matters/${encodeURIComponent(matterId)}/assistant/documents/${encodeURIComponent(d.id)}`, { method: 'DELETE' });
    if (res.status === 401 && !retried) { await api('/api/matters'); return remove(d, true); }   // api() owns the firm sign-in prompt
    if (!res.ok && res.status !== 404) throw new ApiError(res.status, res.statusText || `the server answered ${res.status}`);
  }

  const focusRow = (id, what) => [...list.children].find((li) => li.dataset.id === id)?.querySelector(what)?.focus();

  // The row while it asks: the question, Delete and Keep. Escape keeps.
  function askRow(d) {
    const keep = () => { asking = null; paint(); focusRow(d.id, '.av-file-more'); };
    const del = el('button', { type: 'button', class: 'asx-mini danger', text: 'Delete', 'aria-label': `Delete ${d.title}` });
    del.addEventListener('click', async () => {
      del.disabled = true;
      const at = [...list.children].findIndex((li) => li.dataset.id === d.id);
      try {
        await remove(d);
        if (openId === d.id) closeDocument({ restoreFocus: false });
        asking = null; rows = rows.filter((x) => x.id !== d.id); paint();
        (list.children[Math.min(at, list.children.length - 1)]?.querySelector('.av-file-open') || search).focus?.();
      } catch (err) { asking = null; paint(); toast(`Not deleted: ${err?.message || 'no reply from the server'}`, 'error'); }
    });
    return el('li', { class: 'av-file asx-row asking', 'data-id': d.id, onkeydown: (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); keep(); } } },
      el('span', { class: 'asx-ask', text: 'Delete this file?' }), del, el('button', { type: 'button', class: 'asx-mini av-file-keep', text: 'Keep', onclick: keep }));
  }

  function row(d) {
    if (asking === d.id) return askRow(d);
    const open = el('button', { type: 'button', class: 'av-file-open', 'aria-current': d.id === openId ? 'true' : null, title: `Open ${d.title} as a PDF` },
      el('span', { class: 'av-file-t', text: d.title }),
      el('span', { class: 'av-file-m', text: dot(kindWords(d.kind), d.createdAt ? fmtDateTime(d.createdAt) : null) }),
      // A source count of zero beside entries means the list did not count them, not that there are none: leave it out.
      d.entryCount != null || d.sourceCount ? el('span', { class: 'av-file-m', text: dot(d.entryCount != null ? plural(d.entryCount, 'entry', 'entries') : null, d.sourceCount ? plural(d.sourceCount, 'source') : null) }) : null);
    open.addEventListener('click', () => onOpen?.(d.raw, open));
    const dl = el('button', { type: 'button', class: 'btn quiet sm av-file-dl', text: 'Download', 'aria-label': `Download ${d.title} as a PDF` });
    dl.addEventListener('click', async () => {
      dl.disabled = true;
      try { await downloadDocument(d.raw, matterId); } catch (err) { toast(`Not downloaded: ${err?.message || 'no reply from the server'}`, 'error'); }
      dl.disabled = false;
    });
    const more = el('button', { type: 'button', class: 'av-file-more', text: '⋯', title: 'Delete this file', 'aria-label': `Delete ${d.title}…`, onclick: () => { asking = d.id; paint(); focusRow(d.id, '.av-file-keep'); } });
    return el('li', { class: 'av-file', 'data-id': d.id }, open, el('div', { class: 'av-file-act' }, dl, more));
  }

  function paint() {
    search.hidden = rows.length < SEARCH_FROM;
    if (search.hidden && query) { query = ''; search.value = ''; }
    const shown = rows.filter((d) => !query || d.title.toLowerCase().includes(query));
    list.replaceChildren(...shown.map(row));
    note.replaceChildren();
    if (!rows.length) note.append(el('p', { class: 'empty', text: 'No files yet. Ask for a chronology or a summary and it appears here.' }));
    else if (!shown.length) note.append(el('p', { class: 'empty', text: 'No file title matches the search.' }));
  }

  async function refresh() {
    const mine = ++seq;
    if (!rows.length) { note.replaceChildren(el('div', { class: 'v2-skel av-files-skel', 'aria-hidden': 'true' }, [0, 1, 2].map(() => el('div')))); list.replaceChildren(); }
    try {
      const got = MOCK_ASSISTANT ? mockRows() : await listDocuments(matterId);
      if (gone || mine !== seq) return;
      rows = (Array.isArray(got) ? got : []).filter((r) => r && r.id != null).map((r) => ({ ...describe(r), raw: r }))
        .sort((x, y) => String(y.createdAt || '').localeCompare(String(x.createdAt || '')));
      paint();
    } catch (err) {
      if (gone || mine !== seq) return;
      list.replaceChildren();
      note.replaceChildren(el('p', { class: 'error', text: `The files could not be listed: ${err?.message || 'no reply from the server'}. ` }, el('button', { type: 'button', class: 'as-link', text: 'Retry', onclick: refresh })));
    }
  }

  // A turn made a document. In mock mode there is no server to list it, so the event's own document is kept.
  const onCreated = async (e) => {
    let doc = e?.detail?.document || (e?.detail?.id != null ? e.detail : null);
    if (MOCK_ASSISTANT && doc && !made.some((m) => String(m.id) === String(doc.id))) {
      // The event may carry the id alone: the document itself is in the conversation that made it.
      if (!doc.title) { try { const { chat } = await import('./store.js'); doc = chat.turns.flatMap((t) => t.blocks || []).find((b) => b?.type === 'document' && String(b.id) === String(doc.id)) || doc; } catch { /* keep the id */ } }
      made.unshift({ created_at: new Date().toISOString(), ...doc });
    }
    refresh();
  };
  const onViewer = (e) => {
    openId = e?.detail?.id ?? null;
    for (const li of list.children) { const b = li.querySelector('.av-file-open'); if (li.dataset.id === openId) b?.setAttribute('aria-current', 'true'); else b?.removeAttribute('aria-current'); }
  };
  window.addEventListener(event, onCreated);
  window.addEventListener(VIEWER_EVENT, onViewer);

  // What each kind is called comes from the server; until it answers, rows use the built-in words.
  if (!MOCK_ASSISTANT) api(`/api/matters/${encodeURIComponent(matterId)}/assistant/document-kinds`).then((kinds) => { learnKinds(kinds); if (!gone && rows.length) paint(); }).catch(() => { /* an older server has no such route */ });

  refresh();
  return {
    refresh,
    destroy() { gone = true; window.removeEventListener(event, onCreated); window.removeEventListener(VIEWER_EVENT, onViewer); host.replaceChildren(); },
  };
}
