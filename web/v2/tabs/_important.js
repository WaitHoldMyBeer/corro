// The "mark important" control on the Documents tab.
import { el } from '../../js/util.js';
import { mark, unmark } from '../cards/evidence/_important-api.js';

export function importantControl(doc, ctx, initial) {
  let on = !!initial;
  const b = el('button', { class: 'btn ghost', type: 'button', 'aria-pressed': 'false' });
  const paint = () => { b.setAttribute('aria-pressed', String(on)); b.textContent = on ? 'Important' : 'Mark important'; };
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const prev = on; on = !on; paint(); b.disabled = true;
    try { await (on ? mark : unmark)(ctx, doc.id); } catch (err) { on = prev; paint(); ctx.toast(`Not saved: ${err.message}`, 'error'); }
    b.disabled = false;
  });
  paint();
  return b;
}
