// HTTP wrappers and the single multiplexed event stream.

import { toast } from './util.js';

// The launch key bootstrap.js kept (src/atelier/web/security.py): every request
// of the page's own carries it.
const KEY = 'atelier.controlToken';
let token = null;
try { token = localStorage.getItem(KEY); } catch { /* storage off */ }

export function authHeaders(headers = {}) {
  return token ? { ...headers, 'X-Atelier-Token': token } : headers;
}

// Started again, `atelier serve` has a new key, and the tab it opened keeps it
// for this address: an older tab takes it up — with its reading cookie — once,
// instead of failing.
async function renewed() {
  let stored = null;
  try { stored = localStorage.getItem(KEY); } catch { return false; }
  if (!stored || stored === token) return false;
  try {
    const res = await fetch('/api/session', {
      method: 'POST', headers: { 'X-Atelier-Token': stored } });
    if (!res.ok) return false;
  } catch { return false; }
  token = stored;
  return true;
}

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const send = () => fetch(url, { ...opts, headers: authHeaders(opts.headers) });
  let res = await send();
  if (res.status === 403 && await renewed()) res = await send();
  const ct = res.headers.get('content-type') || '';
  const payload = ct.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = (payload && (payload.detail || payload.error)) || String(payload).slice(0, 300);
    const err = new Error(detail || `${res.status} ${res.statusText}`);
    err.status = res.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

export const get = (url) => request('GET', url);
export const post = (url, body) => request('POST', url, body);

export async function tryPost(url, body, { quiet = false } = {}) {
  try { return await post(url, body); }
  catch (e) { if (!quiet) toast(e.message, 'err'); throw e; }
}

export const api = {
  config: () => get('/api/config'),
  doctor: (live = false) => get(`/api/doctor?live=${live}`),
  problems: () => get('/api/problems'),
  problem: (slug) => get(`/api/problems/${encodeURIComponent(slug)}`),
  createProblem: (body) => post('/api/problems', body),
  files: (slug, subdir = '') =>
    get(`/api/problems/${encodeURIComponent(slug)}/files?subdir=${encodeURIComponent(subdir)}`),
  file: (slug, path) =>
    get(`/api/problems/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`),
  fileUrl: (slug, path) =>
    `/api/problems/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}&raw=1`,
  openExternally: (slug, path) => post(`/api/problems/${encodeURIComponent(slug)}/open`, { path }),
  compile: (slug) => post(`/api/problems/${encodeURIComponent(slug)}/compile`, {}),
  unlock: (slug) => post(`/api/problems/${encodeURIComponent(slug)}/unlock`, {}),

  harnesses: () => get('/api/harnesses'),
  harness: (id) => get(`/api/harnesses/${encodeURIComponent(id)}`),
  preview: (id, body) => post(`/api/harnesses/${encodeURIComponent(id)}/preview`, body),

  launch: (body) => post('/api/runs', body),
  runs: (q = '') => get(`/api/runs${q}`),
  activeRuns: () => get('/api/runs/active'),
  run: (id) => get(`/api/runs/${encodeURIComponent(id)}`),
  runEvents: (id, after = 0) =>
    get(`/api/runs/${encodeURIComponent(id)}/events?after=${after}`),
  runDiff: (id) => get(`/api/runs/${encodeURIComponent(id)}/diff`),
  steer: (id, text) => post(`/api/runs/${encodeURIComponent(id)}/steer`, { text }),
  signal: (id, action, extra = {}) =>
    post(`/api/runs/${encodeURIComponent(id)}/signal`, { action, ...extra }),
  verdict: (id, body) => post(`/api/runs/${encodeURIComponent(id)}/verdict`, body),

  accounts: () => get('/api/accounts'),
  probeAccount: (id) => post(`/api/accounts/${encodeURIComponent(id)}/probe`, {}),
  usage: (group = 'day', days = 30) => get(`/api/usage?group=${group}&days=${days}`),
};

// -------------------------------------------------------------- event stream

/** One SSE connection per tab, multiplexed by topic.
 *  Browsers cap concurrent HTTP/1.1 connections at ~6 per origin, so a stream
 *  per panel would deadlock the app with two tabs open. */
class Stream {
  constructor() {
    this.handlers = new Map();      // topic -> Set<fn>
    this.subId = null;
    this.es = null;
    this.retry = 0;
  }

  topics() { return [...this.handlers.keys()]; }

  on(topic, fn) {
    if (!this.handlers.has(topic)) this.handlers.set(topic, new Set());
    this.handlers.get(topic).add(fn);
    this._sync();
    return () => this.off(topic, fn);
  }

  off(topic, fn) {
    const set = this.handlers.get(topic);
    if (!set) return;
    set.delete(fn);
    if (!set.size) this.handlers.delete(topic);
    this._sync();
  }

  offAll(prefix) {
    for (const topic of [...this.handlers.keys()]) {
      if (topic.startsWith(prefix)) this.handlers.delete(topic);
    }
    this._sync();
  }

  _sync() {
    if (!this.subId) { this.connect(); return; }
    clearTimeout(this._syncTimer);
    this._syncTimer = setTimeout(() => {
      fetch(`/api/stream/${this.subId}/topics`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ topics: this.topics() }),
      }).catch(() => { /* the reconnect path will re-register */ });
    }, 40);
  }

  connect() {
    if (this.es) this.es.close();
    const qs = encodeURIComponent(this.topics().join(',') || 'runs');
    this.es = new EventSource(`/api/stream?topics=${qs}`);

    this.es.addEventListener('hello', (e) => {
      try { this.subId = JSON.parse(e.data).sub_id; } catch { /* ignore */ }
      this.retry = 0;
      this._sync();
    });

    this.es.addEventListener('event', (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      const set = this.handlers.get(msg.topic);
      if (!set) return;
      for (const fn of set) {
        try { fn(msg); } catch (err) { console.error('stream handler', err); }
      }
    });

    this.es.onerror = () => {
      this.es.close();
      this.es = null;
      this.subId = null;
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 400 * 2 ** this.retry);
    };
  }
}

export const stream = new Stream();
