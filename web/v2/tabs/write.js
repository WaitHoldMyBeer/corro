// Write: the checked editor from web/js/write.js, housed in the v2 shell with a standing panel,
// "Against the documents", beside it. The editor, its modes, the lock banner, Send and its reason
// prompt are the existing ones, reused as they are. The panel reads the underlined sentences the editor
// has already drawn (each carries the server's verdict, evidence and replacement); it asks nothing new
// of the server and never touches the text except through the one-click replacement.
import { renderWrite, VERDICT } from '../js/write.js';
import { chip } from '../../js/drawer.js';
import { el } from '../../js/util.js';

function ensureStyles() {
  if (document.getElementById('nw-styles')) return;
  const link = document.createElement('link');
  link.id = 'nw-styles'; link.rel = 'stylesheet';
  link.href = new URL('../css/notes-write.css', import.meta.url).href;
  document.head.append(link);
}

const AGAINST = new Set(['contradicted', 'out_of_date']);

export default function mount(host, c, ctx) {
  ensureStyles();
  const inner = el('div', {});
  host.replaceChildren(el('div', { class: 'v2-page-h' }, el('h1', { text: 'Write' })), inner);
  const ed = renderWrite(inner, c, { matterId: ctx.matterId });

  const cols = inner.querySelector('.write-cols');
  const mirror = inner.querySelector('.ed-mirror');
  const ta = inner.querySelector('.ed-input');
  const select = inner.querySelector('.write-bar select');
  cols.classList.add('wr-cols');
  inner.setAttribute('data-ai-skip', '');   // a draft in progress never goes to the assistant

  const againstList = el('ol', { class: 'wr-list' });
  const lockedList = el('ol', { class: 'wr-list' });
  const lockedSec = el('section', { class: 'wr-sec', hidden: true },
    el('h3', { text: 'Locked for this reader' }),
    el('p', { class: 'small muted', text: 'These sentences would disclose something this provider is not shown.' }), lockedList);
  const panel = el('aside', { class: 'card inner wr-panel', 'aria-label': 'Against the documents' },
    el('section', { class: 'wr-sec' },
      el('h3', { text: 'Against the documents' }),
      el('p', { class: 'small muted', text: 'Sentences the file contradicts or has since replaced, with the page.' }),
      el('p', { class: 'small muted wr-status', role: 'status' }),
      againstList),
    lockedSec);
  cols.querySelector('aside')?.replaceWith(panel);

  // The underline key sits right under the editor, with a plain label.
  const legend = inner.querySelector('.legend');
  const foot = inner.querySelector('.ed-foot');
  if (legend && foot) {
    legend.prepend(el('span', { class: 'small muted', style: 'margin-right: var(--sp-2)', text: 'Underlines:' }));
    foot.after(legend);
  }

  const slice = (s) => ta.value.slice(s.start, s.end);
  function useValue(s) {
    // Only if the sentence is still where the server saw it.
    if (slice(s) !== s.text) return;
    ta.focus();
    ta.setRangeText(s.replacement.text, s.start + s.replacement.rel, s.start + s.replacement.rel + s.replacement.len, 'end');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function item(s, locked) {
    const v = VERDICT[s.verdict];
    const shown = s.text.length > 140 ? `${s.text.slice(0, 138)}...` : s.text;
    return el('li', { class: `wr-item m-${s.verdict}` },
      el('div', { class: 'wr-v', text: locked ? 'do not send' : v.short }),
      el('blockquote', { class: 'wr-sentence', text: shown }),
      s.line ? el('p', { class: 'small', text: s.line }) : null,
      (s.evidence || []).slice(0, 2).map((e) => chip(e.ref)),
      s.replacement && !locked ? el('button', { class: 'btn small primary', type: 'button', onclick: () => useValue(s) }, `Use the file's value: ${s.replacement.text}`) : null);
  }

  // A line that says a check is running, from the keystroke until the answer repaints the editor.
  // Its height is reserved, so nothing moves; it clears itself if no answer comes.
  const status = panel.querySelector('.wr-status');
  let statusTimer = null;
  const setStatus = (text) => { status.textContent = text; clearTimeout(statusTimer); if (text) statusTimer = setTimeout(() => { status.textContent = ''; }, 8000); };
  ta.addEventListener('input', () => { if (ta.value.trim()) setStatus('Checking...'); else setStatus(''); });

  // The editor rewrites its footer only when an answer lands: that ends the wait.
  if (foot) new MutationObserver(() => setStatus('')).observe(foot, { childList: true, characterData: true, subtree: true });

  let sig = '';
  function sync() {
    // While any sentence is still being checked, leave the panel as it is: it settles when the answer lands.
    if (mirror.querySelector('.vd.checking')) return;
    const spans = [...mirror.querySelectorAll('.vd')].map((n) => n._span).filter(Boolean);
    const forProvider = select.value.startsWith('provider:');
    const against = spans.filter((s) => AGAINST.has(s.verdict)).map((s) => ({ ...s, text: slice(s) }));
    const locked = forProvider ? spans.filter((s) => s.verdict === 'dont_send').map((s) => ({ ...s, text: slice(s) })) : [];
    const next = JSON.stringify([forProvider, against.map((s) => [s.start, s.end, s.verdict, s.text, s.replacement?.text]), locked.map((s) => [s.start, s.end, s.text])]);
    if (next === sig) return;
    sig = next;
    againstList.replaceChildren(...(against.length ? against.map((s) => item(s, false)) : [el('li', { class: 'wr-none', text: spans.length ? 'No sentence here is against the file.' : 'Sentences that disagree with a document appear here, with the page.' })]));
    lockedSec.hidden = !forProvider;
    lockedList.replaceChildren(...(locked.length ? locked.map((s) => item(s, true)) : [el('li', { class: 'wr-none', text: 'Nothing is locked.' })]));
  }

  let t = null;
  const queue = () => { clearTimeout(t); t = setTimeout(sync, 60); };
  const mo = new MutationObserver(queue);
  mo.observe(mirror, { childList: true });
  select.addEventListener('change', queue);
  sync();

  return { destroy: () => { mo.disconnect(); clearTimeout(t); clearTimeout(statusTimer); ed.destroy?.(); } };
}
