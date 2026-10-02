// Dashboard search: a plain substring search over what the case model already holds. No server call, no model.
import { el } from '../../js/util.js';

function entries(c, ctx) {
  const out = [];
  const add = (group, label, sub, act) => { if (label) out.push({ group, label: String(label), sub: sub ? String(sub) : '', act, hay: `${label} ${sub || ''}`.toLowerCase() }); };
  for (const e of c.timeline || []) add('Timeline', e.label, ctx.fmt.date(e.date), e.sources?.[0] ? () => ctx.openSource(e.sources[0]) : null);
  for (const f of c.custom_fields || []) add('Fields', f.label, f.display, f.sources?.[0] ? () => ctx.openSource(f.sources[0]) : () => ctx.openTab('fields'));
  for (const f of c.key_facts || []) add('Key facts', f.display, f.label, f.sources?.[0] ? () => ctx.openSource(f.sources[0]) : null);
  for (const d of c.documents || []) add('Documents', d.name, d.folder, d.source ? () => ctx.openSource(d.source) : () => ctx.openTab('documents'));
  for (const p of c.contacts || []) add('People', p.name, p.role_text || p.role, () => ctx.openTab('communications', { contact: p.id }));
  for (const m of c.moves || []) add('Next steps', m.title, m.reason, m.source ? () => ctx.openSource(m.source) : null);
  for (const a of [...(c.agenda?.overdue || []), ...(c.agenda?.coming || []), ...(c.agenda?.waiting || [])]) add('Tasks', a.title, ctx.fmt.date(a.due), a.source ? () => ctx.openSource(a.source) : () => ctx.openTab('tasks'));
  const claims = new Map((c.claims || []).map((x) => [x.id, x]));
  for (const x of c.conflicts || []) {
    const refs = [...(x.document_claim_ids || []), ...(x.notes_claim_ids || [])].map((id) => claims.get(id)?.source).filter(Boolean);
    add('For review', x.topic, x.summary, refs.length ? () => ctx.openSources(refs, x.topic) : null);
  }
  return out;
}

export function mountSearch(host, getCase, ctx) {
  const input = el('input', { type: 'search', placeholder: 'Search the matter: people, dates, documents, amounts, topics', 'aria-label': 'Search the matter', autocomplete: 'off' });
  const results = el('div', { class: 'v2-results', hidden: true, role: 'listbox' });
  host.append(el('span', { class: 'ico', 'aria-hidden': 'true' }), input, results);
  let idx = null, idxFor = null, timer = null;

  function run() {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) { results.hidden = true; return; }
    const c = getCase();
    if (idxFor !== c) { idx = entries(c, ctx); idxFor = c; }
    const terms = q.split(/\s+/);
    const hits = idx.filter((e) => terms.every((t) => e.hay.includes(t)));
    const groups = new Map();
    for (const h of hits) { if (!groups.has(h.group)) groups.set(h.group, []); if (groups.get(h.group).length < 6) groups.get(h.group).push(h); }
    results.replaceChildren(...(groups.size ? [...groups].flatMap(([g, list]) => [
      el('h4', { text: g }),
      ...list.map((h) => el('button', { type: 'button', onclick: () => { results.hidden = true; h.act?.(); } },
        el('span', { text: h.label.length > 110 ? `${h.label.slice(0, 108)}...` : h.label }), h.sub ? el('span', { class: 'r-sub', text: h.sub.slice(0, 40) }) : null)),
    ]) : [el('p', { class: 'v2-empty', text: 'Nothing in the matter matches that.' })]));
    results.hidden = false;
  }
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 120); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.value = ''; results.hidden = true; } });
  document.addEventListener('click', (e) => { if (!host.contains(e.target)) results.hidden = true; });
  return { focus: () => input.focus() };
}
