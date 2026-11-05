// DOM helpers, formatting, toasts, and math rendering.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  // Tolerate h('strong', 'text') — a non-plain-object second argument is a
  // child, not an attribute bag. Without this, Object.entries on a string
  // produces setAttribute('0', 'c') and the DOM throws.
  if (attrs !== null && attrs !== undefined
      && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...nodes) { clear(el).append(...nodes.flat(Infinity).filter(Boolean)); return el; }

// ---------------------------------------------------------------- formatting

export function usd(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  if (n === 0) return '$0.00';
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function int(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString();
}

export function dur(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const s = Math.floor(Number(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

export function ago(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const d = (Date.now() - then) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  if (d < 86400 * 7) return `${Math.floor(d / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function clock(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' :
    d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function truncate(s, n = 120) {
  s = String(s ?? '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ------------------------------------------------------------------- toasts

export function toast(message, kind = '') {
  // A showModal() dialog lives in the browser's top layer. No z-index can put
  // the page-level toast container above its backdrop, so put modal errors in
  // the active dialog itself.
  const dialogs = document.querySelectorAll('dialog[open]');
  const modal = dialogs.length ? dialogs[dialogs.length - 1] : null;
  let wrap = modal ? modal.querySelector(':scope > .dialog-toast-wrap') : null;
  if (modal && !wrap) {
    wrap = h('div', { class: 'toast-wrap dialog-toast-wrap', 'aria-live': 'polite' });
    modal.append(wrap);
  }
  wrap ||= document.getElementById('toasts');
  if (!wrap) return;
  const el = h('div', {
    class: `toast ${kind}`,
    text: message,
    role: kind === 'err' ? 'alert' : 'status',
  });
  wrap.append(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s';
    el.style.opacity = '0';
    setTimeout(() => {
      el.remove();
      if (wrap.classList.contains('dialog-toast-wrap') && !wrap.children.length) wrap.remove();
    }, 260);
  }, kind === 'err' ? 7000 : 3500);
}

// --------------------------------------------------------------------- math

const KATEX_OPTS = {
  delimiters: [
    { left: '$$', right: '$$', display: true },
    { left: '\\[', right: '\\]', display: true },
    { left: '$', right: '$', display: false },
    { left: '\\(', right: '\\)', display: false },
  ],
  throwOnError: false,
  ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
};

export function typeset(root) {
  if (!root || !window.renderMathInElement) return;
  // Server-rendered math arrives in .math spans from markdown-it-dollarmath.
  root.querySelectorAll('.math').forEach(el => {
    const tex = el.textContent;
    const display = el.classList.contains('block') || el.tagName === 'DIV';
    try {
      window.katex.render(tex, el, { displayMode: display, throwOnError: false });
    } catch { /* leave the source visible rather than blowing up the page */ }
  });
  try { window.renderMathInElement(root, KATEX_OPTS); } catch { /* ignore */ }
}

// -------------------------------------------------------------- components

export function dot(status, live = false) {
  return h('span', { class: `dot ${status || 'open'}${live ? ' live' : ''}` });
}

export function stat(label, value, extra) {
  return h('div', { class: 'stat' },
    h('div', { class: 'stat-k', text: label }),
    h('div', { class: 'stat-v', text: value }),
    extra || null);
}

export function meter(fraction) {
  const f = Math.max(0, Math.min(1.2, Number(fraction) || 0));
  const cls = f >= 1 ? 'meter over' : f >= 0.8 ? 'meter hot' : 'meter';
  return h('div', { class: cls }, h('i', { style: { width: `${Math.min(100, f * 100)}%` } }));
}

export function card(title, ...body) {
  return h('div', { class: 'card' }, title ? h('h3', { text: title }) : null, ...body);
}

export function empty(message) { return h('div', { class: 'empty', text: message }); }

export function table(headers, rows) {
  return h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, headers.map(hd =>
      h('th', { class: hd.num ? 'num' : '' }, hd.label ?? hd)))),
    h('tbody', {}, rows));
}

export function renderDiff(text) {
  const lines = String(text || '').split('\n').map(line => {
    let cls = '';
    if (line.startsWith('+++') || line.startsWith('---')) cls = 'meta';
    else if (line.startsWith('@@')) cls = 'hunk';
    else if (line.startsWith('+')) cls = 'add';
    else if (line.startsWith('-')) cls = 'del';
    else if (line.startsWith('diff ') || line.startsWith('index ')) cls = 'meta';
    return `<span class="${cls}">${esc(line)}</span>`;
  });
  return h('div', { class: 'diff', html: lines.join('\n') });
}

export function debounce(fn, ms = 200) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
