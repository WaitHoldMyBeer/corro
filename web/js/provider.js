// The provider's tokenised link: read-only view of what the firm sent, plus a
// reply box per request. No login (stated in the README).
import { api, showMockBanner, MOCK } from './api.js';
import { el, empty } from './util.js';
import { renderProviderView } from './providerview.js';

showMockBanner();
const host = document.getElementById('view');
const token = new URLSearchParams(location.search).get('token') || 'mock';

function visitKey() { return `lastVisit:${token}`; }
function readVisit() { try { return localStorage.getItem(visitKey()); } catch { return null; } }
function writeVisit(v) { try { localStorage.setItem(visitKey(), v); } catch { /* storage may be blocked */ } }

const lastVisit = readVisit();

function draw(view) {
  renderProviderView(host, view, {
    preview: false,
    lastVisit,
    onRequest: async (r) => { const next = await api(`/api/share/${encodeURIComponent(token)}/requests`, { method: 'POST', body: r }); shown = JSON.stringify(next); draw(next); },
    onAsk: async (text) => { const next = await api(`/api/share/${encodeURIComponent(token)}/messages`, { method: 'POST', body: { text } }); shown = JSON.stringify(next); draw(next); },
    onReply: async (askId, text) => {
      const next = await api(`/api/share/${encodeURIComponent(token)}/asks/${encodeURIComponent(askId)}/reply`, { method: 'POST', body: { text } });
      draw(next);
    },
  });
}

let shown = '';
try {
  const view = await api(`/api/share/${encodeURIComponent(token)}`);   // the one call that logs the open
  document.title = `Case update for ${view.provider?.name || 'your office'}`;
  shown = JSON.stringify(view);
  draw(view);
  writeVisit(new Date().toISOString());
} catch (err) {
  host.replaceChildren(el('div', { class: 'card' },
    el('h1', { text: err.status === 410 ? 'This link is no longer active' : 'This update could not be opened' }),
    empty(err.status === 410 ? 'It has expired or the firm withdrew it. Please contact the firm for a new link.' : err.message)));
}

// Quiet refresh: the non-counting read, only re-drawn when something changed and nobody is typing a reply.
setInterval(async () => {
  if (document.hidden || host.contains(document.activeElement) || MOCK) return;
  try {
    const v = await api(`/api/share/${encodeURIComponent(token)}?peek=1`);
    const s = JSON.stringify(v);
    if (s !== shown) { shown = s; draw(v); }
  } catch { /* the link may have been withdrawn; the next full load says so */ }
}, 8000);
