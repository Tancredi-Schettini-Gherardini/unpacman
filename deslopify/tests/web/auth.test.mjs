// Run with: node --experimental-vm-modules --test deslopify/tests/web/auth.test.mjs
// The page's key on every request, and a newer one taken up once (dom.js,
// 2026-10-20) — with a stand-in fetch and storage, no server.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/dom.js', import.meta.url), 'utf8');

async function load({ token, stored, answers }) {
  const calls = [];
  const storage = stored === 'off'
    ? { getItem() { throw new Error('storage off'); } }
    : { getItem: key => (key === 'unpacman.controlToken' ? stored ?? null : null) };
  const window = { unpacmanToken: token };
  const fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', headers: { ...(opts.headers || {}) }, body: opts.body });
    const [status, body] = answers.shift() ?? [500, '{"detail":"no answer left"}'];
    return { status, ok: status >= 200 && status < 300, statusText: String(status), text: async () => body };
  };
  class FormData {}
  const context = vm.createContext({ window, fetch, localStorage: storage, FormData, JSON, Promise, Error });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => { throw new Error('No imports expected'); });
  await module.evaluate();
  return { api: module.namespace.api, authFetch: module.namespace.authFetch, calls, window };
}

const REFUSED = [403, '{"detail":"Open unpacman from its launcher or the URL printed by its server."}'];

test('every request carries the key, reading ones too', async () => {
  const { api, calls } = await load({ token: 'K1', answers: [[200, '[]']] });
  assert.deepEqual(await api('GET', '/api/papers'), []);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].headers['X-Unpacman-Token'], 'K1');
});

test('without a key, no header is made up', async () => {
  const { api, calls } = await load({ token: undefined, answers: [[200, '{}']] });
  await api('GET', '/api/hello');
  assert.equal('X-Unpacman-Token' in calls[0].headers, false);
});

test('refused, a newer key in this browser is taken up and the request sent again', async () => {
  const { api, calls, window } = await load({
    token: 'OLD', stored: 'NEW', answers: [REFUSED, [200, '{"ok":true}'], [200, '{"done":1}']] });
  assert.deepEqual(await api('POST', '/api/papers/p/read', { level: 'expert' }), { done: 1 });
  assert.deepEqual(calls.map(c => [c.method, c.url, c.headers['X-Unpacman-Token']]), [
    ['POST', '/api/papers/p/read', 'OLD'],
    ['POST', '/api/session', 'NEW'],
    ['POST', '/api/papers/p/read', 'NEW'],
  ]);
  // The same body, and its type, the second time.
  assert.equal(calls[2].body, calls[0].body);
  assert.equal(calls[2].headers['Content-Type'], 'application/json');
  assert.equal(window.unpacmanToken, 'NEW');
});

test('refused with no newer key: said as before, and not sent again', async () => {
  for (const stored of ['OLD', null, 'off']) {
    const { api, calls } = await load({ token: 'OLD', stored, answers: [REFUSED] });
    await assert.rejects(api('GET', '/api/papers'), /Open unpacman from its launcher/);
    assert.equal(calls.length, 1, `stored ${stored}`);
  }
});

test('a newer key the server refuses too is not taken up', async () => {
  const { api, calls, window } = await load({ token: 'OLD', stored: 'STALE', answers: [REFUSED, REFUSED] });
  await assert.rejects(api('GET', '/api/papers'), /Open unpacman/);
  assert.deepEqual(calls.map(c => c.url), ['/api/papers', '/api/session']);
  assert.equal(window.unpacmanToken, 'OLD');
});

test('authFetch hands back the answer, refused or not, for its callers to read', async () => {
  const { authFetch, calls } = await load({ token: 'K', stored: 'K', answers: [REFUSED] });
  const res = await authFetch('/api/report');
  assert.equal(res.status, 403);
  assert.equal(calls[0].headers['X-Unpacman-Token'], 'K');
});
