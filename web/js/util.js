// DOM helpers and display formatting. Text always goes in through textContent /
// text nodes, never innerHTML: source text and email bodies are untrusted.

export function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
export function svgEl(tag, attrs = {}, text) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
export const fmtMoney = (n) => (n == null ? null : usd.format(n));
export const fmtDate = (s) => (s ? String(s).slice(0, 10) : null);

export function fmtDateTime(s) {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s).slice(0, 16).replace('T', ' ');
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function dayWords(days) {
  if (days == null) return null;
  if (days === 0) return 'today';
  if (days < 0) return `${-days} day${days === -1 ? '' : 's'} overdue`;
  return `in ${days} day${days === 1 ? '' : 's'}`;
}

export const empty = (text) => el('p', { class: 'empty', text });

export function pill(text, cls = '') {
  return el('span', { class: `pill ${cls}`.trim(), text });
}

export function statusPill(status) {
  return pill(status || 'unknown', `st-${status || 'unknown'}`);
}

export function section(title, sub, ...body) {
  return el('section', { class: 'card' },
    el('header', { class: 'card-h' }, el('h2', { text: title }), sub ? el('span', { class: 'sub', text: sub }) : null),
    ...body);
}

export function toast(text, kind = 'info') {
  let host = document.getElementById('toasts');
  if (!host) { host = el('div', { id: 'toasts', 'aria-live': 'polite' }); document.body.append(host); }
  const t = el('div', { class: `toast ${kind}`, text });
  host.append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 9000 : 5000);
}

export function initials(name) {
  return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
}
