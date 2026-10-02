// Firm sign-in: a passcode prompt shown when the server answers 401 to a firm call. The cookie is HttpOnly and
// set by the server; nothing is stored here, and the passcode is never kept after the request.
import { el } from './util.js';

let dlg = null;

export function promptPasscode(login) {
  return new Promise((resolve) => {
    if (dlg) { dlg.remove(); dlg = null; }
    const input = el('input', { type: 'password', autocomplete: 'current-password', 'aria-label': 'Firm passcode', placeholder: 'Firm passcode' });
    const msg = el('p', { class: 'error small', role: 'alert' });
    const go = el('button', { class: 'btn primary', type: 'submit' }, 'Sign in');
    const form = el('form', { class: 'signin' },
      el('div', { class: 'corro-logo', text: 'Corro' }),
      el('h2', { text: 'Sign in to the firm view' }),
      el('p', { class: 'muted small', text: 'Enter your firm\'s passcode.' }),
      input, msg, go);
    dlg = el('dialog', { class: 'signin-dlg' }, form);
    dlg.addEventListener('cancel', (e) => e.preventDefault());   // no way to dismiss without signing in
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      go.disabled = true; msg.textContent = '';
      try {
        await login(input.value);
        input.value = '';
        dlg.close(); dlg.remove(); dlg = null;
        resolve();
      } catch (err) {
        msg.textContent = err.status === 429 ? 'Too many wrong passcodes. Wait a minute and try again.' : err.status === 401 ? 'Wrong passcode.' : `Could not sign in: ${err.message}`;
        go.disabled = false; input.select();
      }
    });
    document.body.append(dlg);
    dlg.showModal();
    input.focus();
  });
}
