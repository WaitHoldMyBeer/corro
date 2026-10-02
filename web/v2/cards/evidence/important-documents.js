// The ten documents that matter. The lawyer's own choices come first, in the
// lawyer's order; AI suggestions only fill what is left and are always labelled.
import { loadCss } from '../../tabs/_css.js';
loadCss('tabs.css');

import { loadSlots, accept, reject, reorder } from './_important-api.js';

import { sourceLine } from './_sources.js';
import { docTitle as shown } from '../../tabs/_names.js';

function render(host, slots, ctx, full = false) {
  const { el } = ctx;
  const refresh = async () => {
    try { render(host, await loadSlots(ctx), ctx, full); } catch (err) { ctx.toast(`Could not update: ${err.message}`, 'error'); }
  };
  const act = (fn, id) => async () => { try { await fn(ctx, id); } catch (err) { ctx.toast(`Not saved: ${err.message}`, 'error'); } refresh(); };
  const lawyerIds = () => slots.filter((s) => s.by === 'lawyer').map((s) => s.id);
  const move = async (id, to) => {
    const ids = lawyerIds(); const i = ids.indexOf(id); if (to < 0 || to >= ids.length) return;
    ids.splice(to, 0, ids.splice(i, 1)[0]);
    try { await reorder(ctx, ids); } catch (err) { ctx.toast(`Order not saved: ${err.message}`, 'error'); }
    refresh();
  };
  let dragId = null;
  const rows = (full ? slots : slots.slice(0, 5)).map((s, n) => {
    const mine = s.by === 'lawyer';
    const idx = lawyerIds().indexOf(s.id);
    const li = el('li', { 'data-ai-unit': '', 'data-ai-kind': 'document', 'data-document-id': s.id, 'data-ai-title': s.name, class: `ev-doc ${mine ? 'mine' : 'ai'}`, draggable: mine ? 'true' : null, 'data-id': s.id },
      el('span', { class: 'ev-doc-n', text: String(n + 1) }),
      el('div', { class: 'ev-doc-main' },
        el('button', { class: 'ev-doc-name', type: 'button', onclick: () => s.source && ctx.openSource(s.source), disabled: !s.source, title: s.name, text: shown(s.name) }),
        el('div', { class: `small muted ev-claimline${full ? '' : ' v2-trunc'}`, title: !full && !mine ? s.why : null }, s.pages ? `${ctx.fmt.plural(s.pages, 'page')}. ` : '', mine ? (s.origin === 'accepted' ? 'Accepted from an AI suggestion.' : 'Picked by you.') : (s.why || 'No reason recorded.'), ' ', sourceLine(ctx, [s.source], { max: 1, none: 'No source in the file' }))),
      el('div', { class: 'ev-doc-act' },
        s.uploaded ? ctx.pill('Uploaded') : null,
        mine ? ctx.pill('Your choice', 'ok') : ctx.pill('Suggested by AI', 'ai'),
        !full ? null : mine
          ? [el('button', { class: 'btn ghost', type: 'button', 'aria-label': `Move ${s.name} up`, disabled: idx <= 0, onclick: () => move(s.id, idx - 1), text: 'Up' }),
            el('button', { class: 'btn ghost', type: 'button', 'aria-label': `Move ${s.name} down`, disabled: idx >= lawyerIds().length - 1, onclick: () => move(s.id, idx + 1), text: 'Down' })]
          : [el('button', { class: 'btn small primary', type: 'button', onclick: act(accept, s.id), text: 'Accept' }),
            el('button', { class: 'btn small quiet', type: 'button', onclick: act(reject, s.id), text: 'Reject' })]));
    if (mine) {
      li.addEventListener('dragstart', (e) => { dragId = s.id; e.dataTransfer.effectAllowed = 'move'; });
      li.addEventListener('dragover', (e) => { if (dragId != null && dragId !== s.id) e.preventDefault(); });
      li.addEventListener('drop', (e) => { e.preventDefault(); if (dragId != null && dragId !== s.id) move(dragId, lawyerIds().indexOf(s.id)); dragId = null; });
    }
    return li;
  });
  const ai = slots.filter((s) => s.by !== 'lawyer').length;
  host.replaceChildren(
    el('p', { class: 'muted small', text: `${slots.length - ai} chosen by you, ${ai} suggested by AI to fill the ten. Your choices always come first.` }),
    el('ol', { class: 'ev-docs' }, rows),
    !full && slots.length > 5 ? el('p', { class: 'muted small', text: `${slots.length - 5} more, and Accept, Reject and reordering, in the expanded view.` }) : null);
}

export default {
  id: 'important-documents',
  title: 'Important documents',
  group: 'Documents',
  size: 'l',
  depends: ['documents'],
  empty: (c) => ((c.documents || []).length ? null : 'No documents are on file.'),
  summary(c, ctx) {
    const host = ctx.el('div', { class: 'ev-importants' }, ctx.el('p', { class: 'muted small', text: 'Loading...' }));
    loadSlots(ctx).then((s) => (s.length ? render(host, s, ctx) : host.replaceChildren(ctx.el('p', { class: 'v2-empty', text: 'No documents chosen yet. Mark one from the Documents tab.' }))))
      .catch((err) => host.replaceChildren(ctx.el('p', { class: 'v2-empty', text: `Important documents are not available yet (${err.message}).` })));
    return host;
  },
  detail(c, ctx) {
    const host = ctx.el('div', { class: 'ev-importants' }, ctx.el('p', { class: 'muted small', text: 'Loading...' }));
    loadSlots(ctx).then((s) => render(host, s, ctx, true)).catch((err) => host.replaceChildren(ctx.el('p', { class: 'v2-empty', text: `Not available (${err.message}).` })));
    return host;
  },
};
