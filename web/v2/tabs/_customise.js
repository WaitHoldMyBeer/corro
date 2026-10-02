// Change what a provider sees by typing. The server does the work: it maps the instruction to a closed set of
// operations, refuses what must never be shared, builds any provider-facing wording and returns the new policy.
// This file only sends the instruction with the current policy, shows the reply, and saves the policy it is given.
// It never composes provider text and never switches a locked category on.
import { el, pill } from '../../js/util.js';
import { MOCK } from '../../js/api.js';

const route = (matterId, contactId) => `/api/matters/${matterId}/providers/${contactId}/customise`;
const history = new Map();   // `${matter}:${contact}` -> turns, kept across the re-render that follows each change
let scopeMatter = null;
let scope = null;            // { kind, id?, label } or null: what the next instruction is about
let strips = new Set();

export function setScope(next, focus = true) {
  scope = next;
  for (const s of strips) s.paintScope(focus);
}

// ctx: the tab ctx; provider(): the selected provider panel from the case model; apply(): re-render after a change.
export function customiseStrip(ctx, provider, apply) {
  if (scopeMatter !== ctx.matterId) { scope = null; scopeMatter = ctx.matterId; }   // nothing carries over from another case
  const key = () => `${ctx.matterId}:${provider().contact.id}`;
  const turns = el('ol', { class: 'cz-turns', 'aria-live': 'polite' });
  const scopeBox = el('span', { class: 'cz-scope' });
  const input = el('input', { type: 'text', class: 'cz-input', maxlength: 400, 'aria-label': 'Describe a change to what this provider sees', placeholder: 'Describe a change, e.g. hide the two oldest bills' });
  const status = el('div', { class: 'cz-status small muted', role: 'status' });
  const box = el('section', { class: 'card cz' }, el('header', { class: 'card-h' }, el('h2', { text: 'Change by describing it' }), el('span', { class: 'sub', text: 'the preview changes; nothing is sent' })),
    el('div', { class: 'cz-row' }, scopeBox, input), status, turns);

  const strip = {
    paintScope(focus) {
      scopeBox.replaceChildren(...(scope ? [el('span', { class: 'cz-pill scope' }, `About: ${scope.label}`,
        el('button', { type: 'button', 'aria-label': 'Remove the scope', text: 'x', onclick: () => setScope(null) }))] : []));
      if (scope && focus) input.focus();
    },
  };
  strips = new Set([strip]);   // one strip on screen at a time; a re-render makes a new one
  const cid = () => provider().contact.id;

  function paintTurns() {
    turns.replaceChildren(...(history.get(key()) || []).slice(-4).map((t) => el('li', { class: `cz-turn${t.refused ? ' refused' : ''}` },
      el('div', { class: 'small muted', text: `You: ${t.instruction}${t.scope ? ` (about ${t.scope})` : ''}` }),
      t.reply ? el('div', { class: 'cz-reply', text: t.reply }) : null,
      ...(t.refusals || []).map((x) => el('div', { class: 'cz-refusal', text: x })),
      t.changes.length ? el('div', { class: 'cz-pills' }, ...t.changes.map((ch) => el('span', { class: 'cz-pill' }, ch)),
        t.undone ? el('span', { class: 'small muted', text: 'undone' }) : el('button', { type: 'button', class: 'btn small quiet', text: 'Undo', onclick: () => undo(t) })) : null)));
  }

  async function save(policy) {
    await ctx.api(`/api/matters/${ctx.matterId}/providers/${cid()}/policy`, { method: 'PUT', body: policy });
  }
  async function undo(t) {
    try { await save(t.before); t.undone = true; await apply(); } catch (err) { status.textContent = `Not undone: ${err.message}`; }
  }

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    const p = provider();
    const before = structuredClone(p.policy);
    status.textContent = 'Working...'; input.disabled = true;
    const turn = { instruction: text, scope: scope?.label || null, reply: '', changes: [], refused: false, before };
    try {
      // Only a thing the server can name is sent as the target; a whole section or a preview block is a whole-share instruction.
      const named = scope && ['ask', 'update', 'category', 'cover_note'].includes(scope.kind) && scope.id;
      const body = { instruction: text, target: named ? { kind: scope.kind, id: scope.id } : null, draft_policy: before };
      const r = MOCK ? await standIn(ctx, p, body) : await ctx.api(route(ctx.matterId, p.contact.id), { method: 'POST', body });
      const refusals = (r.refused || r.refusals || []).map((x) => x.reason || String(x));
      turn.changes = (r.operations || r.changes || []).map((ch) => ch.summary || ch.label || ch.verb);
      turn.refused = refusals.length > 0 && !turn.changes.length;
      turn.refusals = refusals;
      turn.reply = turn.changes.length ? (r.reply || 'Done.') : (refusals.length ? '' : (r.reply || 'Nothing to change.'));
      history.set(key(), [...(history.get(key()) || []), turn]);
      input.value = ''; status.textContent = '';
      // The real route saves the policy itself; the mock stand-in does not, so it is saved here.
      if (turn.changes.length) { if (MOCK && r.policy) await save(r.policy); paintTurns(); await apply(); }
      else paintTurns();
    } catch (err) {
      status.textContent = err.status === 404 || err.status === 405
        ? 'Describing a change is not available on this server yet. Use the switches and fields below.'
        : `Nothing was changed: ${err.message}`;
    } finally { input.disabled = false; input.focus(); }
  }
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  strip.paintScope(false); paintTurns();
  return { node: box, input };
}

// A small control that scopes the next instruction to one thing.
export function changeButton(label, target) {
  return el('button', { type: 'button', class: 'cz-change', 'aria-label': `Change: ${label}`, text: 'Change...', onclick: () => setScope({ ...target, label }) });
}

// ---- ?mock=1 only: a few phrase patterns standing in for the server, so the strip can be tried without the model.
// The wording it produces is generic and lives in the mock, as the real wording lives on the server.
async function standIn(ctx, p, body) {
  await new Promise((r) => setTimeout(r, 250));
  const t = body.instruction.toLowerCase();
  const pol = structuredClone(body.draft_policy);
  const cats = new Set(pol.allowed_categories);
  const changes = [];
  const word = { bills: 'Bills', records: 'Records', status: 'Status updates', coverage: 'Coverage', attendance: 'Attendance', asks: 'Requests' };
  const refuse = (reason) => ({ reply: '', refusals: [{ reason }], changes: [], policy: pol });
  if (/(case value|valuation|settle|offer|policy limit|fee|strategy|lien amount)/.test(t)) return refuse('Case value, offers, limits, fees and strategy are never shared with a provider.');
  const cat = Object.keys(word).find((k) => t.includes(k.replace('asks', 'request')) || t.includes(word[k].toLowerCase()) || (k === 'status' && /update/.test(t)));
  if (/(hide|remove|turn off|stop|without)/.test(t) && cat) { cats.delete(cat); changes.push({ summary: `${word[cat]} switched off` }); }
  else if (/(show|add|turn on|include)/.test(t) && cat) { cats.add(cat); changes.push({ summary: `${word[cat]} switched on` }); }
  else if (/(reword|rephrase|shorten)/.test(t) && p.asks?.length && pol.approved_asks) { const a = body.target?.id ? p.asks.find((x) => x.id === body.target.id) : p.asks[0]; pol.approved_asks = { ...pol.approved_asks, [a.id]: 'Placeholder reworded request.' }; changes.push({ summary: 'Request reworded' }); }
  else if (/(hide|remove)/.test(t) && body.target?.id) { pol.hidden_item_ids = [...(pol.hidden_item_ids || []), body.target.id]; changes.push({ summary: 'Item hidden' }); }
  else return refuse('I could not turn that into a change I am allowed to make. Try "hide bills" or "turn on attendance".');
  pol.allowed_categories = [...cats];
  return { reply: 'Done. Check the preview before you send.', refused: [], operations: changes, policy: pol };
}
