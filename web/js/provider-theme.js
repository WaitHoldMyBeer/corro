// The provider page's colour scheme control: one quiet button in the header that opens two rows of swatches.
// The backgrounds, accents, swatches and storage all come from the firm shell's theme module; this page keeps its
// choice under its own keys ("pv:theme", "pv:accent", set by data-theme-ns in provider.html's inline snippet), in this
// browser only. Nothing about the choice is sent anywhere.
import { swatchRows, pairName } from '../v2/js/theme.js';

const slot = document.getElementById('pv-theme');
if (slot && !slot.firstChild) {
  const make = (tag, props = {}, ...kids) => { const n = document.createElement(tag); for (const [k, v] of Object.entries(props)) { if (k === 'text') n.textContent = v; else if (k === 'class') n.className = v; else n.setAttribute(k, v); } n.append(...kids); return n; };
  const { bgRow, acRow } = swatchRows();
  const name = make('span', { class: 'pt-name' });
  const btn = make('button', { type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', 'aria-controls': 'pt-pop' }, make('span', { class: 'th-dot', 'aria-hidden': 'true' }), name);
  const pair = make('strong', { class: 'pt-pair', 'aria-live': 'polite' });
  const pop = make('div', { class: 'pt-pop', id: 'pt-pop', role: 'dialog', 'aria-label': 'Colours for this page', hidden: '' },
    pair,
    make('div', { class: 'th-label', text: 'Background' }), bgRow,
    make('div', { class: 'th-label', text: 'Accent' }), acRow,
    make('p', { class: 'pt-note', text: 'Only changes how this page looks in this browser.' }));
  const box = make('span', { class: 'pt' }, btn, pop);

  const paint = () => {
    name.textContent = pairName(); pair.textContent = pairName();
    btn.setAttribute('aria-label', `Colours for this page: ${pairName()}`);
    btn.title = 'Colours for this page';
  };
  const open = (on) => {
    pop.hidden = !on; btn.setAttribute('aria-expanded', String(on));
    if (on) (pop.querySelector('.th-grid [aria-checked="true"]') || pop.querySelector('button')).focus();
  };
  btn.addEventListener('click', () => open(pop.hidden));
  box.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { open(false); btn.focus(); } });
  document.addEventListener('pointerdown', (e) => { if (!pop.hidden && !box.contains(e.target)) open(false); });
  box.addEventListener('focusout', (e) => { if (!pop.hidden && e.relatedTarget && !box.contains(e.relatedTarget)) open(false); });
  window.addEventListener('v2:theme', paint);
  paint();
  slot.append(box);
}
