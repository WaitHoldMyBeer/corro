// Communications: logged emails and calls as a fast list. Each row shows direction, who, the record's own date and a
// one-line excerpt; expanding it loads the full text. Where the server's `incoming` holds a checked sentence for the
// communication, the firm-only "their statement / your file" line sits beside it. Text enters the DOM via textContent only.
import { el, empty, pill } from '../../js/util.js';
import { remoteLoader } from './_remote.js';
import { loadCss } from './_css.js';

const CHUNK = 60;
const load = remoteLoader('communications');
const VERDICT_WORD = { contradicted: 'Contradicts your file', out_of_date: 'Out of date', not_in_file: 'Not in your file', supported: 'Supported by your file' };

// Direction comes from who is a known outside contact on the matter: a sender who is a contact means it came in,
// a receiver who is a contact means it went out. Anything else is shown as logged, with no guess.
function direction(r, contactNames) {
  if (r.meta?.direction === 'received') return 'In';   // the server's own word, when it carries one
  if (r.meta?.direction === 'sent') return 'Out';
  const has = (s) => { const t = String(s || '').toLowerCase(); return [...contactNames].some((n) => t.includes(n)); };
  if (has(r.who || r.meta?.sender)) return 'In';
  if (has(r.meta?.to)) return 'Out';
  return '';
}

export default function mount(host, c, ctx) {
  loadCss('tabs.css'); loadCss('share.css');
  const contactNames = new Set((c.contacts || []).map((x) => String(x.name || '').trim().toLowerCase()).filter(Boolean));
  const checks = new Map((c.incoming || []).map((x) => [x.id, x]));
  let q = '', offset = 0, total = 0, busy = false, done = false, gen = 0;

  const count = el('span', { class: 'tab-count muted small', 'aria-live': 'polite' });
  const search = el('input', { type: 'search', class: 'tab-search', placeholder: 'Search communications', 'aria-label': 'Search communications' });
  const list = el('ol', { class: 'cm-list' });
  const note = el('div', { class: 'tab-note' });
  const sentinel = el('div', { class: 'tab-sentinel', 'aria-hidden': 'true' });

  function row(r) {
    const dir = direction(r, contactNames);
    const check = checks.get(r.id);
    const kind = String(r.meta?.type || 'Communication').replace(/Communication$/, '') || 'Communication';
    const flagged = check && (check.spans || []).some((s) => s.verdict === 'contradicted' || s.verdict === 'out_of_date');
    const btn = el('button', { type: 'button', class: 'cm-head', 'aria-expanded': 'false' },
      el('span', { class: 'cm-main' },
        el('span', { class: 'cm-titlerow' }, el('span', { class: 'cm-title', text: r.title }), dir ? pill(dir === 'In' ? 'Received' : 'Sent', dir === 'In' ? 'st-assumed' : 'ok') : null),
        el('span', { class: 'cm-who muted small', text: [kind, (r.who || r.meta?.sender) && `from ${r.who || r.meta.sender}`, r.meta?.to && `to ${r.meta.to}`].filter(Boolean).join(' ') }),
        r.snippet ? el('span', { class: 'cm-excerpt small', text: r.snippet }) : null),
      el('span', { class: 'cm-date small', title: r.date_is || 'The date held on the logged entry' }, (ctx.fmt.date(r.date) ? `${r.date_is ? r.date_is.charAt(0).toUpperCase() + r.date_is.slice(1) : 'Dated'}: ${ctx.fmt.date(r.date)}` : 'no date')),
      flagged ? pill('checked: differs from file', 'st-contested') : null);
    const body = el('div', { class: 'cm-body', hidden: true });
    const li = el('li', { class: 'cm-row' }, btn, body);
    let loaded = false;
    btn.addEventListener('click', async () => {
      const open = body.hidden;
      body.hidden = !open; btn.setAttribute('aria-expanded', String(open));
      if (!open || loaded) return;
      loaded = true;
      body.replaceChildren(el('p', { class: 'muted small', text: 'Loading the full text...' }));
      let text = r.snippet || '';
      try { const d = await ctx.api(r.source.href); text = d.text || text; } catch { /* the excerpt stands */ }
      body.replaceChildren(...[
        el('div', { class: 'cm-text', text: text || 'This entry has no text.' }),
        check ? checkBlock(check) : null,
        el('button', { class: 'btn small', type: 'button', onclick: () => ctx.openSource(r.source) }, 'Open the entry')].filter(Boolean));
    });
    return li;
  }

  function checkBlock(check) {
    const spans = (check.spans || []).filter((s) => s.verdict && s.verdict !== 'supported');
    return el('div', { class: 'cm-check' },
      el('div', { class: 'eyebrow', text: 'Firm only: their statement against your file' }),
      spans.length ? spans.map((s) => el('div', { class: `annot a-${s.verdict}` },
        el('div', { class: 'eyebrow', text: VERDICT_WORD[s.verdict] || s.verdict }),
        el('div', { class: 'small' }, el('strong', { text: 'Their statement: ' }), s.text),
        s.message ? el('div', { class: 'small' }, el('strong', { text: 'Your file: ' }), s.message) : null,
        (s.sources || []).length ? el('div', { class: 'chips' }, s.sources.map((src) => el('button', { type: 'button', class: 'chip', text: src.label, onclick: () => ctx.openSource(src) }))) : null))
        : el('p', { class: 'small muted', text: 'Checked against the file: nothing to flag.' }));
  }

  async function more(reset) {
    if (busy || (done && !reset)) return;
    busy = true;
    const mine = reset ? ++gen : gen;
    if (reset) { offset = 0; done = false; list.replaceChildren(); }
    try {
      const res = await load(ctx, { offset, limit: CHUNK, q });
      if (mine !== gen) return;
      if (res.available === false) { note.replaceChildren(empty(res.reason || 'Communications are not available for this matter.')); done = true; return; }
      total = res.total ?? offset + res.rows.length;
      offset += res.rows.length;
      done = !res.rows.length || offset >= total;
      list.append(...res.rows.map(row));
      count.textContent = q ? `${offset} of ${total} match` : `${total} communications`;
      note.replaceChildren(total === 0 ? empty(q ? 'Nothing matches that filter.' : 'No communications are on file.') : '');
    } catch (err) {
      if (mine === gen) note.replaceChildren(el('p', { class: 'error', text: `Could not load this list: ${err.message}` }));
    } finally { busy = false; }
    if (!done && sentinel.getBoundingClientRect().top < window.innerHeight + 200) more(false);
  }

  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value; more(true); }, 40); });
  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) more(false); }, { rootMargin: '300px' });
  io.observe(sentinel);
  host.replaceChildren(el('section', { class: 'tab' },
    el('header', { class: 'tab-h' }, el('h2', { text: 'Communications' }), count, search), note, list, sentinel));
  more(true);
  return { destroy: () => { io.disconnect(); clearTimeout(timer); } };
}
