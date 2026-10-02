// A locked sentence is refused by the server (HTTP 423) unless a reason comes with the request. The reason is
// logged with the sentence and never shown to the provider. The same prompt serves every firm-to-provider text.
import { el } from './util.js';

// The server's 423 detail is an object {code, message, locked[]}; api() puts its JSON in err.message.
export function parseLocked(err) {
  try { const d = JSON.parse(err.message); if (d && typeof d === 'object') return d; } catch { /* a plain sentence */ }
  return { message: err.message, locked: [] };
}

export function reasonPrompt(detail, onSubmit) {
  const d = typeof detail === 'string' ? { message: detail, locked: [] } : (detail || { locked: [] });
  const input = el('input', { type: 'text', maxlength: 300, placeholder: 'Reason for sending anyway', 'aria-label': 'Reason for sending anyway' });
  const go = el('button', { class: 'btn small danger', type: 'button' }, 'Send anyway');
  const submit = () => { if (!input.value.trim()) { input.focus(); return; } onSubmit(input.value.trim()); };
  go.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  const box = el('div', { class: 'reason-box', role: 'alert' },
    el('p', { class: 'small', text: d.message || 'Some of this text discloses something this provider is never shown. Edit it, or give a reason to send it anyway.' }),
    (d.locked || []).length ? el('ul', { class: 'locked-list small' }, d.locked.map((l) => el('li', {}, el('em', { text: `"${l.text}"` }),
      l.state === 'unchecked' || l.state === 'unavailable' ? el('span', { class: 'muted', text: '  (could not be checked)' }) : (l.category ? el('span', { class: 'muted', text: `  (${String(l.category).replace('_', ' ')})` }) : null)))) : null,
    el('div', { class: 'row' }, input, go),
    el('p', { class: 'small muted', text: 'The reason is kept in the firm\'s log with the sentence. The provider never sees it.' }));
  queueMicrotask(() => input.focus());
  return box;
}
