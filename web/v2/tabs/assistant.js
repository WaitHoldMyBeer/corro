// The Assistant section: history and files on the left, one conversation in the middle, and the
// document the assistant made opening on the right. The conversation is the same one the side panel
// shows (web/v2/js/assistant/store.js).
import { el, toast } from '../../js/util.js';
import { chat, subscribe, newConversation, openConversation, forget, useMatter } from '../js/assistant/store.js';
import { conversationView } from '../js/assistant/view.js';
import { listConversations, deleteConversation, renameConversation } from '../js/assistant/client.js';

const sameDay = (iso, now) => { const d = new Date(iso); return !Number.isNaN(d.getTime()) && d.toDateString() === now.toDateString(); };

export default function mount(host, caseModel, ctx) {
  useMatter(ctx?.matterId);
  const matterId = ctx?.matterId ?? chat.matterId;
  let latest = caseModel, rows = [], files = null, gone = false;

  // ---- left rail: new chat, search, history, files
  const search = el('input', { type: 'search', class: 'asx-search', placeholder: 'Search chats', 'aria-label': 'Search chats' });
  const history = el('div', { class: 'asx-history' });
  const filesHost = el('div', { class: 'asx-files' });
  const rail = el('aside', { class: 'asx-rail', 'aria-label': 'Chats and files' },
    el('button', { type: 'button', class: 'btn asx-new', onclick: () => { newConversation(); view.focus(); } }, el('span', { 'aria-hidden': 'true', text: '+' }), 'New chat'),
    search, history, el('h3', { class: 'asx-h', text: 'Files' }), filesHost);

  // ---- right pane: the document viewer (web/v2/js/assistant/viewer.js); a plain rendering stands in if it cannot load
  const pane = el('aside', { class: 'as-docpane', 'aria-label': 'Document' });
  async function openDoc(doc, opener) {
    if (!doc || gone) return;
    try {
      const m = await import('../js/assistant/viewer.js');
      m.openDocument(doc, { mount: pane, matterId, opener });
    } catch {
      const { documentBlock } = await import('../js/assistant/render.js');
      const close = el('button', { type: 'button', class: 'btn sm', text: 'Close', onclick: () => { pane.replaceChildren(); pane.classList.remove('av-open'); } });
      pane.replaceChildren(el('div', { class: 'asx-plain' }, close, Array.isArray(doc.entries) ? documentBlock(doc, { openRef: (ref) => ctx.openSource(ref), showInGraph: () => {}, toggleWide: () => {}, isWide: () => true, toast, save: async () => {} }) : el('p', { class: 'muted', text: 'This document cannot be shown here.' })));
      pane.classList.add('av-open');
    }
  }

  const view = conversationView({ variant: 'page', openDocument: openDoc, getCase: () => latest });
  const root = el('div', { class: 'asx', 'data-ai-skip': true }, rail, el('main', { class: 'asx-main' }, view.node), pane);
  host.append(root);

  // The section fills the space beside the navigation and under the top bar, whatever the shell's padding is.
  function place() {
    const box = document.getElementById('v2-view')?.getBoundingClientRect();
    const main = document.querySelector('.v2-main')?.getBoundingClientRect();
    root.style.top = `${Math.max(0, Math.round((box?.top ?? 0) + window.scrollY))}px`;
    root.style.left = `${Math.round(main?.left ?? 0)}px`;
  }
  window.scrollTo(0, 0);
  place();
  window.addEventListener('resize', place);

  // ---- history: each row opens its chat; its menu renames or deletes it, confirmed in the row itself
  let menu = null;                                   // { id, mode: 'menu' | 'rename' | 'confirm' }
  const setMenu = (id, mode) => { menu = id ? { id, mode } : null; paintHistory(); };
  function row(r) {
    const mode = menu?.id === r.id ? menu.mode : null;
    if (mode === 'rename') {
      const input = el('input', { type: 'text', class: 'asx-rename', value: r.title, maxlength: '120', 'aria-label': 'New name for this chat' });
      const save = async () => {
        const title = input.value.trim();
        if (!title || title === r.title) { setMenu(null); return; }
        try { await renameConversation(matterId, r.id, title); r.title = title; } catch (err) { toast(`Not renamed: ${err.message}`, 'error'); }
        setMenu(null);
      };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } else if (e.key === 'Escape') { e.stopPropagation(); setMenu(null); } });
      input.addEventListener('blur', () => { if (menu?.id === r.id && menu.mode === 'rename') save(); });
      setTimeout(() => { input.focus(); input.select(); }, 0);
      return el('li', { class: 'asx-row editing' }, input);
    }
    if (mode === 'confirm') {
      return el('li', { class: 'asx-row asking' }, el('span', { class: 'asx-ask', text: 'Delete this chat?' }),
        el('button', { type: 'button', class: 'asx-mini danger', text: 'Delete', onclick: async () => {
          try { await deleteConversation(matterId, r.id); forget(r.id); rows = rows.filter((x) => x !== r); } catch (err) { toast(`Not deleted: ${err.message}`, 'error'); }
          setMenu(null);
        } }),
        el('button', { type: 'button', class: 'asx-mini', text: 'Keep', onclick: () => setMenu(null) }));
    }
    if (mode === 'menu') {
      return el('li', { class: 'asx-row asking' },
        el('button', { type: 'button', class: 'asx-mini', text: 'Rename', onclick: () => setMenu(r.id, 'rename') }),
        el('button', { type: 'button', class: 'asx-mini danger', text: 'Delete', onclick: () => setMenu(r.id, 'confirm') }),
        el('span', { class: 'as-grow' }),
        el('button', { type: 'button', class: 'asx-mini', 'aria-label': 'Close the menu', text: '×', onclick: () => setMenu(null) }));
    }
    return el('li', { class: 'asx-row' },
      el('button', { type: 'button', class: 'asx-conv', 'aria-current': r.id === chat.conversationId ? 'true' : null, title: r.title, onclick: () => { openConversation(r.id); paintHistory(); view.focus(); } }, el('span', { class: 'asx-conv-t', text: r.title })),
      el('button', { type: 'button', class: 'asx-del', title: 'Rename or delete', 'aria-label': `Rename or delete the chat “${r.title}”`, text: '⋯', onclick: () => setMenu(r.id, 'menu') }));
  }
  function paintHistory() {
    const q = search.value.trim().toLowerCase();
    const list = rows.filter((r) => !q || r.title.toLowerCase().includes(q));
    const now = new Date();
    const group = (label, items) => (items.length ? [el('h3', { class: 'asx-h', text: label }), el('ul', { class: 'asx-list' }, items.map(row))] : []);
    const today = list.filter((r) => r.updatedAt && sameDay(r.updatedAt, now));
    const earlier = list.filter((r) => !today.includes(r));
    history.replaceChildren(...group('Today', today), ...group('Earlier', earlier));
    if (!list.length) history.append(el('p', { class: 'asx-none', text: q ? 'No chat matches.' : 'No chats yet.' }));
  }
  async function loadHistory() {
    try { rows = await listConversations(matterId); } catch { rows = rows || []; }
    if (!gone && !menu) paintHistory();
  }
  search.addEventListener('input', paintHistory);

  const off = subscribe((type, data) => {
    if (type === 'history') loadHistory();
    else if (type === 'reset') paintHistory();
    else if (type === 'document') openDoc(data, null);
  });
  paintHistory(); loadHistory();
  import('../js/assistant/files.js').then((m) => { if (!gone) files = m.filesList(filesHost, { matterId, onOpen: (doc, opener) => openDoc(doc, opener) }); })
    .catch(() => { filesHost.append(el('p', { class: 'asx-none', text: 'The list of files could not be loaded.' })); });

  if (chat.pendingDoc) { const doc = chat.pendingDoc; chat.pendingDoc = null; openDoc(doc, null); }
  view.focus();

  return {
    update(_changed, next) { latest = next; },
    destroy() {
      gone = true; off(); view.destroy(); files?.destroy?.();
      window.removeEventListener('resize', place);
      import('../js/assistant/viewer.js').then((m) => m.closeDocument()).catch(() => {});
      root.remove();
    },
  };
}
