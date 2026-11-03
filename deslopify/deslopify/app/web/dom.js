/* Small helpers shared by the reader's modules: building elements, talking to
 * the server, typesetting mathematics. No framework, as everywhere here. */

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const mount = (el, ...kids) => { el.replaceChildren(...kids.flat(Infinity).filter(Boolean)); return el; };

export function toast(message, kind = '') {
  const wrap = $('#toasts');
  const el = h('div', { class: `toast ${kind}`, text: message, role: kind === 'err' ? 'alert' : 'status' });
  wrap.append(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 320);
  }, kind === 'err' ? 7000 : 3600);
}

// The key this page was opened with (bootstrap.js) goes with every request it
// makes, reading ones too (2026-10-20): what the page does never rests on the
// session cookie, which only the browser's own loads need — a link opened in
// a tab of its own, an image (security.py).
const TOKEN_KEY = 'unpacman.controlToken';

export function authHeaders(headers = {}) {
  const token = window.unpacmanToken;
  return typeof token === 'string' && token ? { ...headers, 'X-Unpacman-Token': token } : headers;
}

// unpacman restarted since this page was opened, and was opened again from its
// icon — in another tab, which left this browser the new key: take it up.
async function renewed() {
  let stored = null;
  try { stored = localStorage.getItem(TOKEN_KEY); } catch { return false; }
  if (!stored || stored === window.unpacmanToken) return false;
  try {
    const r = await fetch('/api/session', { method: 'POST', headers: { 'X-Unpacman-Token': stored } });
    if (!r.ok) return false;
  } catch { return false; }
  window.unpacmanToken = stored;
  return true;
}

// fetch(), with the key — and, refused, once more with a newer key if this
// browser has one. Bodies here are strings or FormData, which can be sent again.
export async function authFetch(url, opts = {}) {
  const send = () => fetch(url, { ...opts, headers: authHeaders(opts.headers || {}) });
  const res = await send();
  if (res.status === 403 && await renewed()) return send();
  return res;
}

export async function api(method, url, body, { signal } = {}) {
  const opts = { method, headers: {}, signal };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await authFetch(url, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || text || res.statusText;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

// Plain text with its inline $…$ formulas as `.math` elements, which
// `typeset` then draws — for text from an AI shown outside rendered Markdown
// (a search's results, the questions before a reading).
export function mathText(text) {
  return String(text ?? '').split(/(\$[^$\n]{1,300}\$)/).filter(Boolean).map(part =>
    (/^\$[^$\n]+\$$/.test(part) ? h('span', { class: 'math', text: part.slice(1, -1) }) : part));
}

export function typeset(root) {
  if (!root || !window.katex) return;
  root.querySelectorAll('.math').forEach(el => {
    const tex = el.textContent;
    const display = el.classList.contains('block') || el.tagName === 'DIV';
    try { window.katex.render(tex, el, {
      displayMode: display, throwOnError: false,
      // Match the exporter's Tate--Shafarevich symbol. Keep macro state local
      // to this formula; a model-authored definition must not affect others.
      macros: { '\\Sha': '\\mathord{\\text{Ш}}' },
    });
      // KaTeX positions \tag absolutely at the right edge, where it can
      // overlap a wide formula. Keep its formula spans together and give the
      // tag a separate layout cell; the whole display can scroll if needed.
      if (display) {
        const html = el.querySelector('.katex-display > .katex > .katex-html');
        const tag = html?.querySelector(':scope > .tag');
        if (tag) {
          const formula = document.createElement('span');
          formula.className = 'equation-body';
          while (html.firstChild !== tag) formula.append(html.firstChild);
          html.insertBefore(formula, tag);
          html.classList.add('numbered-equation');
        }
      }
    }
    catch { /* leave the source readable rather than blanking the page */ }
  });
}

// "4.2 MB": a size on disk, in the units a reader expects.
export function bytes(n) {
  const value = Number(n) || 0;
  if (value < 1000) return `${value} bytes`;
  const units = ['kB', 'MB', 'GB', 'TB'];
  let x = value / 1000;
  let i = 0;
  while (x >= 1000 && i < units.length - 1) { x /= 1000; i += 1; }
  return `${x >= 100 ? Math.round(x) : x.toFixed(1)} ${units[i]}`;
}

/* A question with one way forward and one back, as a modal dialog. Resolves
 * true for `yes`; false for Cancel, Escape, or a click beside the dialog. The
 * way back has the keyboard, so a stray Enter deletes nothing. `body` is
 * paragraphs (strings) or elements; empty ones are left out. */
export function confirmAction({ title, body = [], yes, danger = false }) {
  return new Promise((resolve) => {
    let answer = false;
    const dialog = h('dialog', { class: 'settings confirm', 'aria-labelledby': 'confirm-title' });
    const cancel = h('button', { class: 'ghost', type: 'button', text: 'Cancel',
                                 onclick: () => dialog.close() });
    const ok = h('button', { class: `primary${danger ? ' danger' : ''}`, type: 'button', text: yes,
                             onclick: () => { answer = true; dialog.close(); } });
    mount(dialog, h('div', { class: 'settings-form' },
      h('h2', { id: 'confirm-title', text: title }),
      body.filter(Boolean).map(p => (typeof p === 'string' ? h('p', { class: 'set-intro', text: p }) : p)),
      h('div', { class: 'set-foot' }, cancel, ok)));
    dialog.addEventListener('close', () => { dialog.remove(); resolve(answer); });
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
