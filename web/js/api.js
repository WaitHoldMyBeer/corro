// Every call to the server goes through here. Mock mode (?mock=1) swaps the
// network for placeholder data generated from the contract schema.
import { mockRoute } from './mock.js';
import { promptPasscode } from './auth.js';

const MOCK_PARAM = new URLSearchParams(location.search).get('mock');
export const MOCK = MOCK_PARAM === '1' || MOCK_PARAM === 'empty';   // 'empty' = a matter before any digest

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

let signin = null;
export const isSigningIn = () => signin !== null;

// One prompt at a time, however many calls (including the quiet refetch) hit a 401 together.
function signIn() {
  if (!signin) signin = promptPasscode((passcode) => api('/api/firm/login', { method: 'POST', body: { passcode }, noAuthRetry: true })).finally(() => { signin = null; });
  return signin;
}

export async function api(path, { method = 'GET', body, noAuthRetry = false } = {}) {
  if (MOCK) {
    try { return await mockRoute(path, method, body); } catch (err) {
      if (err.status === 401 && !noAuthRetry && !path.startsWith('/api/firm/')) { await signIn(); return api(path, { method, body, noAuthRetry: true }); }
      throw err;
    }
  }
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* keep statusText */ }
    // The firm's own calls need a session; the provider's link (/api/share) never does.
    if (res.status === 401 && !noAuthRetry && !path.startsWith('/api/share') && !path.startsWith('/api/firm/')) {
      await signIn();
      return api(path, { method, body, noAuthRetry: true });
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

// Links between our own pages keep mock mode on.
export function keepMock(url) {
  if (!MOCK) return url;
  return url + (url.includes('?') ? '&' : '?') + `mock=${MOCK_PARAM}`;
}

export function showMockBanner() {
  if (!MOCK) return;
  const b = document.createElement('div');
  b.className = 'mock-banner';
  b.textContent = 'MOCK DATA: placeholder values generated from the contract schema. Nothing here is real.';
  document.body.prepend(b);
}
