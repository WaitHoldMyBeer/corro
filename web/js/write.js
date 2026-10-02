// The checked editor. One component, used by Write (notes, letters, call notes)
// and by the Share message thread. The real input is a plain <textarea>; the
// underlines live in a mirror layer behind it, so they can never move the caret
// or swallow a keystroke. All verdicts come from the server.
import { api, MOCK } from './api.js';
import { reasonPrompt, parseLocked } from './override.js';
import { el, fmtDate, pill, toast } from './util.js';
import { chip } from './drawer.js';

export const VERDICT = {
  supported: { word: 'Supported by the file', short: 'supported' },
  contradicted: { word: 'The file says otherwise', short: 'contradicted' },
  out_of_date: { word: 'Out of date', short: 'out of date' },
  not_in_file: { word: 'Not in the file', short: 'not in file' },
  dont_send: { word: 'Do not send to this reader', short: 'do not send' },
  checking: { word: 'Checking...', short: 'checking' },
  notyet: { word: 'Not checked yet: the model had not answered', short: 'not checked yet' },
  unchecked: { word: 'Not checked: nothing to compare this sentence with', short: 'not checked' },
  notread: { word: 'Not checked for disclosure', short: 'not checked' },
};

// ---- the server's reply, normalised in one place so the contract can move without touching the UI
function toSpan(sp) {
  const evidence = (sp.evidence || []).map((e) => ({
    role: e.role, text: e.text, date: e.date, category: e.category,
    ref: { ...e.source, date: e.date || e.source?.date },
  }));
  const rep = sp.replacement
    ? { rel: sp.replacement.start - sp.start, len: sp.replacement.end - sp.replacement.start, text: sp.replacement.text }
    : null;
  return {
    id: sp.id, start: sp.start, end: sp.end, verdict: sp.verdict || (sp.pending ? null : (sp.message ? 'notread' : 'unchecked')), pending: !!sp.pending, partial: !!sp.partial,
    factVerdict: sp.fact_verdict, category: sp.category, line: sp.message || '', evidence, replacement: rep, tier: sp.tier,
  };
}

const ROLE_WORD = {
  supports: 'The file says', contradicts: 'The file says', superseded: 'Older source', supersedes: 'Newer source', discloses: 'Discloses',
};

export function createCheckedEditor({ matterId, getAudience, mode = 'draft', placeholder = '', initial = '', onChange, onStats, onResult }) {
  const ta = el('textarea', { class: 'ed-input', spellcheck: 'true', placeholder, 'aria-label': 'Text to check', rows: 10 });
  ta.value = initial;
  const mirror = el('div', { class: 'ed-mirror', 'aria-hidden': 'true' });
  const locks = el('div', { class: 'ed-locks', 'aria-hidden': 'true' });
  const wrap = el('div', { class: 'ed-wrap' }, mirror, locks, ta);
  const margin = el('ol', { class: 'ed-margin', 'aria-label': 'Checked sentences' });
  let spans = [];                       // current underlines, offsets into ta.value
  let last = null;                      // last CheckResult, for the footer
  let prevText = initial;
  let seq = 0, timer = null, card = null, currentMode = mode, unavailable = null;

  const fit = () => { ta.style.height = 'auto'; ta.style.height = `${Math.max(ta.scrollHeight, 220)}px`; };

  // Keep underlines attached to their words while the user types between server replies.
  function shiftSpans(oldT, newT) {
    let p = 0;
    const max = Math.min(oldT.length, newT.length);
    while (p < max && oldT[p] === newT[p]) p += 1;
    let q = 0;
    while (q < max - p && oldT[oldT.length - 1 - q] === newT[newT.length - 1 - q]) q += 1;
    const oldEnd = oldT.length - q, delta = newT.length - oldT.length;
    spans = spans.map((s) => {
      if (s.end <= p) return s;
      if (s.start >= oldEnd) return { ...s, start: s.start + delta, end: s.end + delta };
      return { ...s, end: Math.max(s.start, s.end + delta), verdict: null, pending: true, replacement: null, evidence: s.evidence, edited: true };
    }).filter((s) => s.end > s.start);
  }

  function paintMirror() {
    const text = ta.value;
    const sorted = [...spans].sort((a, b) => a.start - b.start);
    const parts = [];
    let pos = 0;
    for (const s of sorted) {
      if (s.start < pos || s.end > text.length) continue;
      if (s.start > pos) parts.push(document.createTextNode(text.slice(pos, s.start)));
      const node = el('span', { class: `vd ${s.verdict || 'checking'}` }, text.slice(s.start, s.end));   // 'unchecked' draws no underline
      node._span = s;
      parts.push(node);
      pos = s.end;
    }
    parts.push(document.createTextNode(`${text.slice(pos)}​`));   // keeps a trailing empty line the same height
    mirror.replaceChildren(...parts);
  }

  function paintMargin() {
    const rows = spans.filter((s) => s.verdict !== 'unchecked').map((s) => {
      const v = VERDICT[s.verdict || 'checking'];
      const ev = s.evidence?.[0];
      const sentence = ta.value.slice(s.start, s.end);
      return el('li', { class: `m-${s.verdict || 'checking'}` },
        el('span', { class: 'm-v' }, s.verdict === 'dont_send' ? el('span', { class: 'lock-ico', 'aria-hidden': 'true' }) : null, v.short),
        el('span', { class: 'm-t', text: sentence.length > 70 ? `${sentence.slice(0, 68)}...` : sentence }),
        ev ? chip(ev.ref) : null);
    });
    margin.replaceChildren(...(rows.length ? rows : [el('li', { class: 'm-empty', text: 'Checked sentences appear here with their sources.' })]));
  }

  // Lock icons are drawn in their own layer, measured from the underlined text, so they add no width
  // to the mirror and the text can never drift from the textarea above it.
  function paintLocks() {
    locks.replaceChildren();
    const wr = wrap.getBoundingClientRect();
    for (const node of mirror.querySelectorAll('.vd.dont_send')) {
      const r = node.getClientRects()[0];
      if (!r) continue;
      locks.append(el('span', { class: 'lock-pin', style: `top:${Math.round(r.top - wr.top + (r.height - 19) / 2)}px`, title: 'Locked: will not send to this reader' }));
    }
  }
  const repaint = () => { paintMirror(); paintMargin(); paintLocks(); };

  function footer() {
    if (!last) return;
    const st = last.stats || {};
    onStats?.({
      checked: spans.filter((x) => !x.partial || x.verdict).length, tier1: st.tier1_p50_ms, tier2: st.tier2_p50_ms, cost: st.cost_usd ?? 0,
      tier2Available: last.tier2_available, tier2Error: last.tier2_error, ledgerClaims: last.ledger_claims, model: st.model, error: unavailable, blocked: last.blocked,
    });
  }

  async function call(text, maxTier, complete) {
    const aud = getAudience();
    return api(`/api/matters/${matterId}/check`, { method: 'POST', body: {
      text, mode: currentMode, audience: aud.kind, audience_contact_id: aud.kind === 'provider' ? aud.contact_id : null,
      max_tier: maxTier, complete,
    } });
  }

  function apply(res, textAtSend) {
    if (ta.value !== textAtSend) return false;           // the user kept typing: a newer run will follow
    spans = res.spans.map(toSpan);
    last = res; unavailable = null;
    repaint(); footer(); onResult?.(res);
    return true;
  }

  // As-you-type runs leave the trailing fragment alone (complete=false): an amount still being typed is not checked
  // digit by digit and no paid model call goes out for half a sentence. When the lawyer stops (pause, blur, before
  // Send) or in Call notes, the whole text is treated as finished.
  async function run(complete = currentMode === 'call') {
    const my = ++seq;
    const text = ta.value;
    if (!text.trim()) { spans = []; last = null; repaint(); onResult?.(null); return; }
    try {
      const first = await call(text, 1, complete);
      if (my !== seq || !apply(first, text)) return;
      if (first.pending > 0 && first.tier2_available) {
        const second = await call(text, 2, complete);
        if (my !== seq) return;
        // whatever the model did not answer by its deadline is "not checked yet", never left looking cleared
        if (apply(second, text)) { spans = spans.map((s) => (s.pending && !s.verdict ? { ...s, pending: false, verdict: 'notyet' } : s)); repaint(); }
      }
    } catch (err) {
      unavailable = err.message; footer();
    }
  }

  let settle = null;
  const schedule = () => {
    clearTimeout(timer); clearTimeout(settle);
    timer = setTimeout(() => run(false), 300);
    settle = setTimeout(() => run(true), 1200);        // stopped typing: now the last fragment counts too
  };

  ta.addEventListener('input', () => {
    fit();
    shiftSpans(prevText, ta.value);
    prevText = ta.value;
    repaint();
    onChange?.(ta.value);
    schedule();
  });
  ta.addEventListener('blur', () => { clearTimeout(settle); if (ta.value.trim()) run(true); });
  ta.addEventListener('scroll', () => { mirror.scrollTop = ta.scrollTop; });

  // Hover: hit-test the underlined spans' own rectangles (the textarea sits on top).
  function spanAt(x, y) {
    for (const node of mirror.querySelectorAll('.vd')) {
      for (const r of node.getClientRects()) {
        if (x >= r.left - 1 && x <= r.right + 1 && y >= r.top - 2 && y <= r.bottom + 2) return node;
      }
    }
    return null;
  }
  let hoverNode = null, hideTimer = null;
  wrap.addEventListener('mousemove', (e) => {
    const n = spanAt(e.clientX, e.clientY);
    if (n === hoverNode) return;
    hoverNode = n;
    if (!n) { hideTimer = setTimeout(closeCard, 250); return; }
    clearTimeout(hideTimer);
    openCard(n._span, n);
  });
  wrap.addEventListener('mouseleave', () => { hideTimer = setTimeout(closeCard, 250); });

  function closeCard() { card?.remove(); card = null; hoverNode = null; }
  function openCard(s, node) {
    if (!s) return;
    card?.remove();
    const v = VERDICT[s.verdict || 'checking'];
    const evs = (s.evidence || []).map((e) => el('div', { class: 'ev' },
      el('div', { class: 'small muted', text: `${ROLE_WORD[e.role] || e.role}${e.date ? `, ${fmtDate(e.date)}` : ''}` }),
      e.text ? el('div', { class: 'ev-t', text: e.text }) : null,
      chip(e.ref)));
    card = el('div', { class: `ed-card c-${s.verdict || 'checking'}`, role: 'dialog' },
      el('div', { class: 'ed-card-h' }, el('strong', { text: v.word }), s.category ? pill(s.category.replace('_', ' '), '') : null),
      s.line ? el('p', { text: s.line }) : null,
      s.verdict === 'dont_send' && s.factVerdict ? el('p', { class: 'small muted', text: `Whether it is true: ${(VERDICT[s.factVerdict] || {}).short || s.factVerdict}.` }) : null,
      ...evs,
      s.replacement ? el('button', { class: 'btn small primary', type: 'button', onclick: () => { replace(s); closeCard(); } }, `Use the file's value: ${s.replacement.text}`) : null);
    card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    card.addEventListener('mouseleave', () => { hideTimer = setTimeout(closeCard, 250); });
    const wr = wrap.getBoundingClientRect();
    const r = node.getClientRects()[0];
    wrap.append(card);
    card.style.left = `${Math.max(0, Math.min(r.left - wr.left, wr.width - 340))}px`;
    card.style.top = `${r.bottom - wr.top + 6}px`;
  }
  function replace(s) {
    const a = s.start + s.replacement.rel, b = a + s.replacement.len;
    ta.focus();
    ta.setRangeText(s.replacement.text, a, b, 'end');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }

  fit(); repaint();
  if (initial) schedule();

  return {
    root: wrap, margin,
    get value() { return ta.value; },
    setValue(v) { ta.value = v; shiftSpans(prevText, v); prevText = v; fit(); repaint(); onChange?.(v); schedule(); },
    append(text) {
      const cur = ta.value;
      const sep = !cur || /\s$/.test(cur) ? '' : ' ';
      let t = text.trim();
      if (!t) return;
      t = t[0].toUpperCase() + t.slice(1);
      if (!/[.!?]$/.test(t)) t += '.';
      this.setValue(`${cur}${sep}${t} `);
    },
    setMode(m) { currentMode = m; spans = []; repaint(); schedule(); },
    recheck() { spans = []; repaint(); schedule(); },
    focus() { ta.focus(); },
    async finalize() { clearTimeout(timer); clearTimeout(settle); await run(true); },
    get blocked() { return !!last?.blocked && spans.some((s) => s.verdict === 'dont_send'); },
    get pending() { return spans.some((s) => s.pending); },
    destroy() { clearTimeout(timer); card?.remove(); },
  };
}

// ---- the Write surface

const handoff = { text: null, audience: null };
export function openWriteWith(text, audience) { handoff.text = text; handoff.audience = audience; location.hash = '#write'; }

// Speech into the Call-notes box. Browser feature, demo only: the browser sends audio to its vendor's
// speech service. Typed notes remain the primary path and need none of this.
function micControl(getEd) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el('button', { class: 'btn', type: 'button', 'aria-pressed': 'false' }, 'Start microphone');
  const status = el('span', { class: 'small', role: 'status' });
  const interim = el('div', { class: 'interim small', 'aria-live': 'polite' });
  const caveat = el('span', { class: 'mic-caveat small', text: 'Demo only: audio is sent to the browser\'s speech service. This machine\'s microphone only. Do not use it to capture another person on a call without their consent; rules vary by state.' });
  const root = el('div', { class: 'mic', hidden: true }, caveat, btn, status, interim);
  let rec = null, wanted = false;
  const final = (text) => { interim.textContent = ''; getEd().append(text); };
  if (MOCK) window.__say = (t) => final(t);        // lets mock mode exercise the path without a microphone
  if (!SR) {
    btn.disabled = true; btn.textContent = 'Microphone unavailable';
    status.textContent = 'This browser has no speech recognition. Type the call notes instead; they are checked the same way.';
    return { root, stop() {} };
  }
  function stop(msg) {
    wanted = false;
    try { rec?.stop(); } catch { /* already stopped */ }
    btn.setAttribute('aria-pressed', 'false'); btn.textContent = 'Start microphone'; interim.textContent = '';
    if (msg) status.textContent = msg;
  }
  function start() {
    rec = new SR();
    rec.continuous = true; rec.interimResults = true; rec.lang = 'en-US';
    rec.onresult = (e) => {
      let live = '';
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        const r = e.results[i];
        if (r.isFinal) final(r[0].transcript); else live += r[0].transcript;
      }
      interim.textContent = live ? `Hearing: ${live}` : '';
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') stop('Microphone permission was refused. Typed notes still work.');
      else if (e.error !== 'no-speech' && e.error !== 'aborted') stop(`Speech recognition stopped (${e.error}). Typed notes still work.`);
    };
    rec.onend = () => { if (wanted) { try { rec.start(); } catch { stop(); } } };   // the browser ends the session after silence
    wanted = true; status.textContent = 'Listening...';
    try { rec.start(); } catch (err) { stop(`Could not start: ${err.message}`); return; }
    btn.setAttribute('aria-pressed', 'true'); btn.textContent = 'Stop microphone';
  }
  btn.addEventListener('click', () => (wanted ? stop('Stopped.') : start()));
  const onVis = () => {
    if (!root.isConnected) { document.removeEventListener('visibilitychange', onVis); return; }   // the editor was left: drop this listener
    if (document.hidden && wanted) stop('Stopped because the tab was hidden.');
  };
  document.addEventListener('visibilitychange', onVis);
  return { root, stop };
}

export function renderWrite(host, c, ctx) {
  const providers = c.providers || [];
  let audience = { kind: 'internal', contact_id: null };
  if (handoff.audience) audience = handoff.audience;
  const select = el('select', { 'aria-label': 'Who is this for' },
    el('option', { value: 'internal', text: 'Internal note (firm only)' }),
    ...providers.map((p) => el('option', { value: `provider:${p.contact.id}`, text: `Provider: ${p.contact.name}` })),
  );
  select.value = audience.kind === 'provider' ? `provider:${audience.contact_id}` : audience.kind;
  select.addEventListener('change', () => {
    const v = select.value;
    audience = v.startsWith('provider:') ? { kind: 'provider', contact_id: Number(v.slice(9)) } : { kind: v, contact_id: null };
    banner.hidden = audience.kind !== 'provider'; ed.recheck(); note(); updateSend();
  });
  let mode = 'draft';
  const mic = micControl(() => ed);
  const modeBtns = ['draft', 'call'].map((m) => el('button', { type: 'button', 'aria-pressed': String(m === mode), onclick: () => { mode = m; modeBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(['draft', 'call'][i] === m))); ed.setMode(m); note(); mic.root.hidden = m !== 'call'; if (m !== 'call') mic.stop(); } }, m === 'draft' ? 'Draft' : 'Call notes'));
  const hint = el('p', { class: 'small muted' });
  const banner = el('div', { class: 'lock-banner off', role: 'alert', hidden: audience.kind !== 'provider' }, ' ');
  const audienceName = () => (audience.kind === 'provider' ? (providers.find((p) => p.contact.id === audience.contact_id)?.contact.name || 'this provider') : 'this reader');
  const footer = el('div', { class: 'ed-foot small muted', role: 'status' });
  const leakLine = el('div', { class: 'small warn-text', role: 'status' });
  const sendNote = el('span', { class: 'small muted', role: 'status' });
  const sendBtn = el('button', { class: 'btn primary', type: 'button', disabled: true }, 'Send');
  const sendRow = el('div', { class: 'send-row', hidden: audience.kind !== 'provider' }, sendBtn, sendNote);
  function updateSend() {
    sendRow.hidden = audience.kind !== 'provider';
    if (sendRow.hidden) return;
    const empty = !ed.value.trim();
    const locked = ed.blocked;
    sendBtn.disabled = empty || ed.pending;
    sendBtn.textContent = locked ? 'Send anyway, with a reason...' : `Send to ${audienceName()}`;
    sendBtn.classList.toggle('danger', locked);
    sendNote.textContent = empty ? '' : locked ? 'Rewrite the locked sentences, or send anyway with a reason that will be logged.' : ed.pending ? 'Checking...' : 'Nothing locked. Ready to send.';
  }
  const reasonHere = el('div', { hidden: true });
  async function doSend(reason) {
    await ed.finalize();                      // the whole text, trailing fragment included, is checked before it goes
    const text = ed.value.trim();
    sendBtn.disabled = true; sendNote.textContent = 'Sending...';
    try {
      await api(`/api/matters/${ctx.matterId}/providers/${audience.contact_id}/messages`, { method: 'POST', body: { text, ...(reason ? { override_reason: reason } : {}) } });
      reasonHere.hidden = true;
      ed.setValue(''); toast(`Sent to ${audienceName()}. It is on their page and in the Share thread.`);
    } catch (err) {
      if (err.status === 423) { reasonHere.replaceChildren(reasonPrompt(parseLocked(err), (why) => { reasonHere.hidden = true; doSend(why); })); reasonHere.hidden = false; sendNote.textContent = ''; }
      else sendNote.textContent = `Not sent: ${err.message}`;
    }
    updateSend();
  }
  sendBtn.addEventListener('click', async () => {
    await ed.finalize();
    if (ed.blocked) { reasonHere.replaceChildren(reasonPrompt('Some sentences here disclose something this provider is never shown.', (why) => { reasonHere.hidden = true; doSend(why); })); reasonHere.hidden = false; return; }
    doSend(null);
  });
  const ed = createCheckedEditor({
    matterId: ctx.matterId, getAudience: () => audience, mode,
    placeholder: 'Write here. Each sentence is checked against the file as you finish it.',
    initial: handoff.text || '',
    onStats: (s) => {
      if (s.error) { footer.textContent = `Checker unavailable: ${s.error}`; footer.title = ''; return; }
      const ms = s.tier2 ?? s.tier1;
      const typical = ms == null ? null : ms < 5 ? 'instant' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
      footer.textContent = `${s.checked} sentence${s.checked === 1 ? '' : 's'} checked${typical ? `  ·  typical time ${typical}` : ''}  ·  cost so far $${s.cost.toFixed(4)}${s.tier2Available === false ? (s.tier2Error ? '  ·  model unavailable: amounts and dates only' : '  ·  basic checks only: no model configured') : ''}${s.ledgerClaims === 0 ? '  ·  the file has no claims yet: run the digest' : ''}`;
      const leak = s.tier2Available === false && audience.kind === 'provider' ? 'Leak guard is checking figures and names only.' : '';
      leakLine.textContent = leak;
      footer.title = `Code checks ${s.tier1 != null ? `${Math.round(s.tier1)} ms` : 'n/a'} median; model checks ${s.tier2 != null ? `${Math.round(s.tier2)} ms` : 'n/a'} median.`;
    },
    onChange: () => updateSend(),
    onResult: (res) => {
      const n = res ? res.spans.filter((x) => x.verdict === 'dont_send').length : 0;
      banner.hidden = audience.kind !== 'provider';
      banner.classList.toggle('off', n === 0);
      updateSend();
      banner.replaceChildren(el('span', { class: 'lock-ico big', 'aria-hidden': 'true' }),
        el('span', {}, el('strong', { text: `${n} sentence${n === 1 ? '' : 's'} locked: ` }), `will not send to ${audienceName()} unless you override.`));
    },
  });
  handoff.text = null; handoff.audience = null;
  function note() {
    hint.textContent = audience.kind === 'provider'
      ? 'Sentences that would disclose strategy, valuation or other parties to this provider are locked.'
      : mode === 'call' ? 'Call notes: type what is said as it is said. Each sentence is checked against the file.' : 'Internal text is checked for accuracy only.';
  }
  note();
  host.replaceChildren(el('div', { class: 'write' },
    el('div', { class: 'write-bar' },
      el('label', {}, el('span', { class: 'eyebrow', text: 'For' }), select),
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Mode' }, modeBtns),
      mic.root),
    hint,
    el('div', { class: 'write-cols' },
      el('div', { class: 'write-main' }, banner, ed.root, footer, leakLine, sendRow, reasonHere),
      el('aside', { class: 'card inner' }, el('div', { class: 'eyebrow', text: 'Margin' }), ed.margin)),
    el('div', { class: 'legend small' }, Object.entries(VERDICT).filter(([k]) => k !== 'checking' && k !== 'unchecked' && k !== 'notyet' && k !== 'notread').map(([k, v]) => el('span', { class: `lg vd ${k}`, text: v.short })))));
  return ed;
}
