/* Explanations, asked for in place.
 *
 * Select a word, a symbol or a passage — in the companion or in the paper —
 * right-click it, and choose Explain, or Ask about this… to put a question of
 * your own; any note can then be asked a follow-up, answered under it as a
 * short conversation about the same words. The answer arrives as a note beside the
 * text, never over it (on a narrow screen, in a sheet along the bottom that
 * folds away), and it stays for the rest of the session. A note that has served
 * can be folded down to its term, out of focus, and opened again when the term
 * comes back; the whole panel can be folded into a single button.
 *
 * A question about the paper as a whole needs no selection: the Ask button in
 * the bottom-right corner (Tancredi, 2026-10-23) opens the same small box, and
 * its answer is a note like the others, headed by the question.
 *
 * The browser's own menu is one right-click away: a second right-click while
 * this menu is open lets it through. Nothing here takes the keyboard focus from
 * the text being read. */

import { corner } from './corner.js';
import { $, authFetch, h, mount, typeset } from './dom.js';
import * as settings from './settings.js';

const SESSION = (slug) => `deslopify.notes.${slug}`;   // sessionStorage: the notes
const WIDTH = 'unpacman.notesWidth';                  // localStorage: the width they were given
const MIN_WIDTH = 260;
const MAX_WIDTH = 760;
const ROOTS = '#paper, .pdf-pages, .note-body';
const SHORT = 48;                                 // characters: a term, not a passage

const S = {
  slug: null,
  cards: [],                 // newest first
  els: new Map(),            // uid → element
  minimized: false,
  choices: null,             // the AI menu (GET /api/agents)
  fallback: null,            // who explains this paper by default
  menuSel: null,
  reading: null,             // the reading on screen: {slug, level, run}
  hooks: { openPage() {}, showInText() {}, reading: () => null, kind: () => 'paper' },
};

let panel, list, foot, pill, menu, chip, live, askButton, grip;
let menuAt = 0;                                   // where its scroller was when the menu opened
let menuScroller = window;                        // what scrolls the text it was opened on
let asking = false;                               // the menu holds a question being typed

// ------------------------------------------------------------------ setup

export function init(hooks) {
  Object.assign(S.hooks, hooks);
  // Who explains is one of the Advanced settings; the panel's own menu and
  // the dialog change the same thing.
  settings.onChange((now, patch, source) => {
    if ('explainer' in patch && source !== 'notes') paintFoot();
  });

  list = h('div', { class: 'notes-list' });
  foot = h('div', { class: 'notes-foot' });
  panel = h('aside', { class: 'notes', 'aria-label': 'Explanations', hidden: true },
    h('div', { class: 'notes-head' },
      h('span', { class: 'notes-title' }, 'Explanations ', h('span', { class: 'notes-count' })),
      h('button', { class: 'linkish notes-hide', type: 'button', text: 'Hide',
                    title: 'Fold the explanations away; they stay here',
                    onclick: () => { S.minimized = true; save(); render(); } })),
    list, foot);
  pill = h('button', { class: 'notes-pill', type: 'button', hidden: true,
                       onclick: () => { S.minimized = false; save(); render(); } });
  // Their width, dragged at their left edge where they sit beside the text
  // (Tancredi, 2026-10-23); app.css keeps the text clear of whatever it is.
  grip = h('div', { class: 'notes-grip', role: 'separator', tabindex: '0',
                    'aria-orientation': 'vertical', 'aria-label': 'Width of the explanations',
                    'aria-valuemin': String(MIN_WIDTH), 'aria-valuemax': String(MAX_WIDTH),
                    title: 'Drag to widen or narrow · double-click for the usual width' });
  panel.append(grip);
  resizable();
  menu = h('div', { class: 'ctx', role: 'menu', hidden: true });
  const chipButton = (text, act) => h('button', {
    type: 'button', text, onmousedown: (e) => e.preventDefault(),
    onclick: () => { const sel = selectionHere(null); hideChip(); if (sel) act(sel); } });
  chip = h('div', { class: 'explain-chip', hidden: true },
    chipButton('Explain', ask),
    chipButton('Ask…', (sel) => {
      const r = sel.range.getBoundingClientRect();
      openAsk(sel, r.left, r.bottom + 8);
    }));
  live = h('div', { class: 'sr-only', 'aria-live': 'polite' });
  // A question about the paper as a whole: no selection needed.
  askButton = h('button', { class: 'ask-any', type: 'button', hidden: true,
                            'aria-haspopup': 'dialog', onclick: toggleAskAny },
    h('span', { class: 'ask-any-mark', 'aria-hidden': 'true', text: '?' }),
    h('span', { class: 'ask-any-label', text: 'Ask a question' }));
  document.body.append(panel, menu, chip, live);
  corner().append(pill, askButton);

  document.addEventListener('contextmenu', onContextMenu);
  // A press elsewhere closes the menu — except the right button's, which is
  // the second right-click, and must reach the contextmenu handler with the
  // menu still open so that it can step aside for the browser's own.
  document.addEventListener('mousedown', (e) => {
    if (!menu.hidden && !menu.contains(e.target) && !askButton.contains(e.target)
        && e.button !== 2 && !e.ctrlKey) closeMenu();
  }, true);
  document.addEventListener('keydown', onKey);
  // Scrolling away closes the menu, but not the tail of a scroll already in
  // motion when the right-click came: a trackpad's momentum, or the scroll
  // event the browser delivers a frame late, would shut it the moment it opened.
  // A question being typed is not thrown away by a scroll or by switching to
  // another window; a click elsewhere or Escape still closes it.
  // Scrolling is the window's, or a pane's in the side-by-side view: listened
  // for wherever it happens, and judged against the scroller of the text.
  document.addEventListener('scroll', (e) => {
    hideChip();
    if (menu.hidden || asking) return;
    const from = e.target === document ? window : e.target;
    if (from === menuScroller && Math.abs(scrollOf(from) - menuAt) > 24) closeMenu();
  }, { capture: true, passive: true });
  window.addEventListener('resize', () => { if (!asking) closeMenu(); hideChip(); });
  window.addEventListener('blur', () => { if (!asking) closeMenu(); });

  // On a touch screen there is no right-click: a small button appears under a
  // selection instead.
  const touch = matchMedia('(hover: none) and (pointer: coarse)');
  let wait = 0;
  document.addEventListener('selectionchange', () => {
    if (!touch.matches) return;
    clearTimeout(wait);
    wait = setTimeout(() => { const sel = selectionHere(null); sel ? showChip(sel) : hideChip(); }, 350);
  });

  setInterval(tick, 500);
}

// The width the reader gave the explanations, or null for the usual one: set
// as --notes-user (app.css clamps it to what the window allows), and kept.
function setWidth(px, keep = false) {
  const w = Number.isFinite(px) ? Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, px))) : null;
  const root = document.documentElement;
  if (w === null) root.style.removeProperty('--notes-user');
  else root.style.setProperty('--notes-user', `${w}px`);
  grip.setAttribute('aria-valuenow', String(Math.round(panel.offsetWidth || w || 0)));
  if (!keep) return;
  try {
    if (w === null) localStorage.removeItem(WIDTH);
    else localStorage.setItem(WIDTH, String(w));
  } catch { /* storage off: for this page only */ }
}

function resizable() {
  setWidth(stored(localStorage, WIDTH));
  // Followed on the window, so that a pointer moving faster than the edge is
  // still followed; captured as well where the browser allows it.
  grip.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { grip.setPointerCapture(e.pointerId); } catch { /* followed on the window */ }
    document.body.classList.add('notes-resizing');
    const right = panel.getBoundingClientRect().right;
    const move = (ev) => setWidth(right - ev.clientX);
    const up = () => {
      document.body.classList.remove('notes-resizing');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      setWidth(panel.offsetWidth, true);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });
  grip.addEventListener('dblclick', () => setWidth(null, true));
  grip.addEventListener('focus', () => grip.setAttribute('aria-valuenow', String(panel.offsetWidth)));
  // The edge moves as an arrow points: left widens them, right narrows them.
  grip.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 64 : 16;
    const by = { ArrowLeft: step, ArrowRight: -step }[e.key];
    const to = { Home: MIN_WIDTH, End: MAX_WIDTH }[e.key];
    if (e.key === 'Enter') { e.preventDefault(); setWidth(null, true); return; }
    if (by === undefined && to === undefined) return;
    e.preventDefault();
    setWidth(to ?? panel.offsetWidth + by, true);
  });
}

// A paper view opened: show that paper's notes. Leaving every paper: hide them.
export async function attach(slug) {
  if (S.slug === slug) return;
  S.slug = slug;
  S.els.clear();
  list.replaceChildren();
  const saved = stored(sessionStorage, SESSION(slug)) || {};
  S.cards = Array.isArray(saved.cards) ? saved.cards : [];
  S.minimized = !!saved.minimized;
  S.fallback = null;
  render();
  // Anything still being answered when the page was left is asked again; the
  // server either has the answer by now or joins the request still running.
  S.cards.filter(c => c.status === 'pending' || c.status === 'streaming').forEach(c => run(c));
  S.cards.filter(c => c.status === 'done' && c.more?.status === 'pending').forEach(c => runMore(c));
  S.cards.forEach(c => (c.thread || []).filter(t => t.status === 'pending').forEach(t => runFollow(c, t)));

  const r = S.reading?.slug === slug ? S.reading : null;
  const [menuData, meta] = await Promise.all([
    S.choices ? null : settings.menu(),
    getJSON(`/api/papers/${slug}/explanations${readingQuery(r)}`),
  ]);
  if (S.slug !== slug) return;
  if (menuData) S.choices = menuData.groups || [];
  S.fallback = meta?.default || null;
  paintFoot();
}

// The reading on screen changed: who explains by default is whoever wrote it.
export async function setReading(reading) {
  const changed = JSON.stringify(reading) !== JSON.stringify(S.reading);
  S.reading = reading;
  if (!changed || !reading || reading.slug !== S.slug) return;
  const meta = await getJSON(`/api/papers/${reading.slug}/explanations${readingQuery(reading)}`);
  if (S.reading !== reading) return;
  S.fallback = meta?.default || S.fallback;
  paintFoot();
}

function readingQuery(r) {
  if (!r) return '';
  const q = [r.level && `level=${encodeURIComponent(r.level)}`,
             r.run && `run=${encodeURIComponent(r.run)}`].filter(Boolean);
  return q.length ? `?${q.join('&')}` : '';
}

export function detach() {
  S.slug = null;
  S.cards = [];
  S.els.clear();
  list.replaceChildren();
  render();
}

// "Show in text": find the words again and light them for a moment.
export function flashIn(root, find) {
  const range = root && find ? locate(root, find) : null;
  if (!range) return false;
  // Into view in whatever scrolls it: the window, or a pane.
  elementOf(range.startContainer)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (window.CSS?.highlights && window.Highlight) {
    const mark = new Highlight(range);
    CSS.highlights.set('deslopify-flash', mark);
    setTimeout(() => { if (CSS.highlights.get('deslopify-flash') === mark) CSS.highlights.delete('deslopify-flash'); }, 2400);
  } else {
    const s = getSelection();
    s.removeAllRanges();
    s.addRange(range);
  }
  return true;
}

export function focusNote(uid) {
  const card = S.cards.find(c => c.uid === uid);
  if (!card) return;
  card.state = 'open';
  S.minimized = false;
  save();
  render();
  S.els.get(uid)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  S.els.get(uid)?.classList.add('pulse');
  setTimeout(() => S.els.get(uid)?.classList.remove('pulse'), 1200);
}

// ------------------------------------------------------------ selections

function elementOf(node) {
  return node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
}

// The nearest thing that scrolls this node: a pane, a list — or the window.
function scrollerOf(node) {
  for (let el = elementOf(node); el && el !== document.body; el = el.parentElement) {
    const o = getComputedStyle(el).overflowY;
    if ((o === 'auto' || o === 'scroll') && el.scrollHeight > el.clientHeight) return el;
  }
  return window;
}

const scrollOf = (s) => (s === window ? window.scrollY : s.scrollTop);

// The text of a selection, as a reader would type it: rendered mathematics
// becomes its TeX source again, and a PDF's line breaks become spaces.
function readable(fragment, pdf) {
  fragment.querySelectorAll?.('.katex-display').forEach(d => {
    const tex = d.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
    if (tex != null) d.replaceWith(` $$${tex.trim()}$$ `);
  });
  fragment.querySelectorAll?.('.katex').forEach(k => {
    const tex = k.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
    k.replaceWith(tex != null ? `$${tex.trim()}$` : (k.querySelector('.katex-html')?.textContent || ''));
  });
  if (pdf) fragment.querySelectorAll?.('br').forEach(b => b.replaceWith(' '));
  let text = (fragment.textContent || '').replace(/\s+/g, ' ').trim();
  // A word broken across two lines of the PDF: "exam- ple" is "example".
  if (pdf) text = text.replace(/(\p{L})- (\p{Ll})/gu, '$1$2');
  return text;
}

function around(whole, text, span = 1100) {
  if (whole.length <= span) return whole;
  const i = whole.indexOf(text.slice(0, 40));
  const mid = i >= 0 ? i + text.length / 2 : whole.length / 2;
  const from = Math.max(0, Math.floor(mid - span / 2));
  return whole.slice(from, from + span);
}

function pointInside(range, x, y) {
  for (const r of range.getClientRects()) {
    if (x >= r.left - 4 && x <= r.right + 4 && y >= r.top - 4 && y <= r.bottom + 4) return true;
  }
  return false;
}

// What is selected, if it is something this page can explain.
function selectionHere(point) {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const common = elementOf(range.commonAncestorContainer);
  const host = common?.closest(ROOTS);
  if (!host) return null;
  if (point && !pointInside(range, point.x, point.y)) return null;

  const start = elementOf(range.startContainer);
  const pdf = !!start?.closest('.pdf-pages');
  const r = range.cloneRange();
  if (!pdf) {
    const a = elementOf(r.startContainer)?.closest('.katex');
    const b = elementOf(r.endContainer)?.closest('.katex');
    if (a) r.setStartBefore(a);
    if (b) r.setEndAfter(b);
  }
  const text = readable(r.cloneContents(), pdf);
  if (!text) return null;

  let where;
  let block;
  if (pdf) {
    const page = start.closest('.pdf-page');
    where = { kind: 'paper', page: Number(page?.dataset.page) || undefined };
    block = start.closest('.textLayer');
  } else if (host.classList.contains('note-body')) {
    where = { kind: 'note', note: host.closest('.note')?.dataset.uid };
    block = host;
  } else {
    const sec = start.closest('section.sec');
    const label = sec?.querySelector('h2')?.firstChild?.textContent?.trim();
    where = { kind: 'companion', section: sec?.id?.replace(/^s-/, '') || undefined, label };
    block = start.closest('p, li, dd, dt, blockquote, td, th, figcaption, .finding, .notice') || sec;
  }
  const context = block ? around(readable(block.cloneNode(true), pdf), text) : '';
  const find = range.toString();
  return { text, context, where, anchor: { ...where, find }, range };
}

// ------------------------------------------------------------------- menu

function onContextMenu(e) {
  // The browser's own menu, when it is asked for twice.
  if (!menu.hidden) { closeMenu(); return; }
  // From the menu key or Shift-F10 rather than a pointer: there is no point
  // to test against, and the menu opens under the selection instead.
  const keyboard = e.button !== 2 && !e.ctrlKey;
  const sel = selectionHere(keyboard ? null : { x: e.clientX, y: e.clientY });
  if (!sel) return;
  e.preventDefault();
  hideChip();
  let x = e.clientX;
  let y = e.clientY;
  if (keyboard) {
    const r = sel.range.getBoundingClientRect();
    x = r.left;
    y = r.bottom + 4;
  }
  openMenu(x, y, sel, keyboard);
}

function openMenu(x, y, sel, focus) {
  S.menuSel = sel;
  asking = false;
  menu.setAttribute('role', 'menu');
  menu.removeAttribute('aria-label');
  menu.classList.remove('asking');
  const short = sel.text.length <= SHORT && sel.text.split(' ').length <= 6;
  const item = (label, act) => h('button', {
    class: 'ctx-item', role: 'menuitem', type: 'button', text: label,
    // Pressing a button must not collapse the selection it acts on.
    onmousedown: (ev) => ev.preventDefault(),
    onclick: () => { closeMenu(); act(); },
  });
  mount(menu,
    item(short ? `Explain “${sel.text}”` : 'Explain this passage', () => ask(sel)),
    item('Ask about this…', () => openAsk(sel, x, y)),
    item('Copy', () => copy(sel.text)),
    h('div', { class: 'ctx-hint', text: 'Right-click again for the browser’s menu' }));
  placeMenu(x, y);
  if (focus) menu.querySelector('button')?.focus();
}

function placeMenu(x, y) {
  menu.hidden = false;
  menuScroller = S.menuSel ? scrollerOf(S.menuSel.range.startContainer) : window;
  menuAt = scrollOf(menuScroller);
  const w = menu.offsetWidth;
  const hgt = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - w - 8))}px`;
  menu.style.top = `${Math.max(8, y + hgt + 8 > window.innerHeight ? y - hgt : y)}px`;
}

// A question of the reader's own about the selection: the menu becomes a
// small box to type it in, where the menu was.
function openAsk(sel, x, y) {
  S.menuSel = sel;
  asking = true;
  menu.setAttribute('role', 'dialog');
  menu.setAttribute('aria-label', 'Ask about the selection');
  menu.classList.add('asking');
  const field = h('textarea', {
    class: 'ask-field', rows: '2', maxlength: '600', 'aria-label': 'Your question',
    placeholder: 'What it means, why it holds, where it is used…',
  });
  const submit = () => {
    const q = field.value.trim();
    if (!q) { field.focus(); return; }
    closeMenu();
    askQuestion(sel, q);
  };
  field.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  const quote = sel.text.length > 90 ? `${sel.text.slice(0, 87)}…` : sel.text;
  mount(menu, h('form', { class: 'ask-form', onsubmit: (e) => { e.preventDefault(); submit(); } },
    h('div', { class: 'ask-quote', text: `About “${quote}”` }),
    field,
    h('div', { class: 'ask-foot' },
      h('span', { class: 'ask-keys', text: 'Enter to ask · Shift-Enter for a new line · Esc to close' }),
      h('button', { class: 'primary ask-go', type: 'submit', text: 'Ask' }))));
  placeMenu(x, y);
  // Typing in the box takes the selection away from the text; the passage is
  // kept lit meanwhile, so it stays plain what the question is about.
  if (window.CSS?.highlights && window.Highlight) {
    CSS.highlights.set('deslopify-asked', new Highlight(sel.range.cloneRange()));
  }
  field.focus({ preventScroll: true });
}

function closeMenu() {
  if (menu.hidden) return;
  menu.hidden = true;
  asking = false;
  menu.classList.remove('asking');
  delete menu.dataset.whole;
  askButton.setAttribute('aria-expanded', 'false');
  S.menuSel = null;
  window.CSS?.highlights?.delete('deslopify-asked');
}

// The corner's Ask button: a question of the reader's own about what they
// are reading as a whole — the same small box as for a selection, above the
// button; pressed again, it closes.
function toggleAskAny() {
  if (!menu.hidden && menu.dataset.whole) { closeMenu(); return; }
  openAskAny();
}

function openAskAny() {
  if (!S.slug) return;
  hideChip();
  closeMenu();
  S.menuSel = null;
  asking = true;
  menu.dataset.whole = '1';
  menu.setAttribute('role', 'dialog');
  menu.setAttribute('aria-label', 'Ask a question');
  menu.classList.add('asking');
  const kind = S.hooks.kind(S.slug);
  const field = h('textarea', {
    class: 'ask-field', rows: '3', maxlength: '600', 'aria-label': 'Your question',
    placeholder: kind === 'search' ? 'What these results are, which to read first…'
      : kind === 'collection' ? 'How the papers fit together, what they assume…'
        : 'What the paper proves, what it assumes, where to start…',
  });
  const submit = () => {
    const q = field.value.trim();
    if (!q) { field.focus(); return; }
    closeMenu();
    askAnything(q);
  };
  field.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  mount(menu, h('form', { class: 'ask-form', onsubmit: (e) => { e.preventDefault(); submit(); } },
    h('div', { class: 'ask-quote', text: kind === 'search' ? 'About this search and its results'
      : kind === 'collection' ? 'About these papers, not a passage in particular'
        : 'About the paper, not a passage in particular' }),
    field,
    h('div', { class: 'ask-foot' },
      h('span', { class: 'ask-keys', text: 'Enter to ask · Shift-Enter for a new line · Esc to close' }),
      h('button', { class: 'primary ask-go', type: 'submit', text: 'Ask' }))));
  menu.hidden = false;
  menuScroller = window;
  menuAt = scrollOf(window);
  const r = askButton.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.right - menu.offsetWidth,
                                            window.innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, r.top - menu.offsetHeight - 10)}px`;
  askButton.setAttribute('aria-expanded', 'true');
  field.focus({ preventScroll: true });
}

function onKey(e) {
  if (menu.hidden) return;
  if (asking) {
    if (e.key === 'Escape') {
      e.preventDefault();
      const whole = !!menu.dataset.whole;
      closeMenu();
      if (whole) askButton.focus();
    }
    return;
  }
  const items = [...menu.querySelectorAll('.ctx-item')];
  const at = items.indexOf(document.activeElement);
  if (e.key === 'Escape') { e.preventDefault(); closeMenu(); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); items[(at + 1) % items.length]?.focus(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
}

function copy(text) {
  (navigator.clipboard?.writeText(text) || Promise.reject()).catch(() => document.execCommand('copy'));
}

function showChip(sel) {
  const rects = sel.range.getClientRects();
  const last = rects[rects.length - 1];
  if (!last) return;
  chip.hidden = false;
  chip.style.left = `${Math.max(8, Math.min(last.left, window.innerWidth - chip.offsetWidth - 8))}px`;
  chip.style.top = `${Math.min(last.bottom + 10, window.innerHeight - chip.offsetHeight - 8)}px`;
}

function hideChip() { chip.hidden = true; }

// ------------------------------------------------------------------ asking

function keyOf(text, model, level) {
  return `${level || ''}|${model || ''}|${text.normalize('NFKC').replace(/\s+/g, ' ').trim()
    .replace(/^[\s"'“”‘’«»().,;:]+|[\s"'“”‘’«»().,;:]+$/g, '').toLowerCase()}`;
}

function who() {
  const valid = (p) => p && (S.choices || []).some(g => g.account === p.account
    && (p.custom || g.models.some(m => m.id === p.model)));
  const pick = settings.get().explainer;
  if (valid(pick)) return pick;
  return S.fallback || null;
}

function ask(sel) {
  if (!S.slug) return;
  const by = who();
  // Asked in one reading, answered from its companion and at its level, and
  // "Show in text" goes back to it — wherever the reader has moved since.
  const reading = S.hooks.reading() || S.reading;
  const at = reading && reading.slug === S.slug
    ? { level: reading.level || null, run: reading.run || null } : null;
  const key = keyOf(sel.text, by?.model, at?.level);
  const same = S.cards.find(c => c.key === key && c.status !== 'error');
  if (same) { focusNote(same.uid); return; }
  const card = {
    uid: Math.random().toString(36).slice(2, 10), key,
    text: sel.text, context: sel.context, where: sel.where,
    anchor: { ...sel.anchor, reading: sel.where.kind === 'companion' ? at : null },
    state: 'open', status: 'pending', who: by, reading: at, started: Date.now(),
  };
  // A new question opens its own note and leaves the others as they were.
  S.cards.unshift(card);
  S.minimized = false;
  save();
  render();
  list.scrollTo({ top: 0, behavior: 'smooth' });
  run(card);
}

async function run(card) {
  const slug = S.slug;
  // The first run's suggestion has done its work (firstask.js) — when a
  // selection was asked about, which is what it suggests.
  if (card.where?.kind !== 'whole') window.dispatchEvent(new CustomEvent('unpacman:asked'));
  Object.assign(card, { status: 'pending', error: null, partial: '', started: Date.now() });
  paint(card);
  try {
    await streamLines(`/api/papers/${slug}/explain`, {
      text: card.text, context: card.context || '', where: card.where, ...(card.who || {}),
      level: card.reading?.level || undefined, run: card.reading?.run || undefined,
      question: card.question || undefined,
    }, (ev) => {
      if (S.slug !== slug) return;
      if (ev.type === 'start') {
        card.agent = ev.agent;
        card.status = 'streaming';
      } else if (ev.type === 'delta') {
        card.partial = (card.partial || '') + ev.text;
      } else if (ev.type === 'done') {
        Object.assign(card, { status: 'done', html: ev.html, agent: ev.agent,
                              seconds: ev.seconds, cost: ev.cost, cached: !!ev.cached,
                              partial: '' });
        live.textContent = `Explanation ready: ${card.text.slice(0, 80)}`;
        save();
      } else if (ev.type === 'error') {
        Object.assign(card, { status: 'error', error: ev.message });
        save();
      }
      paint(card);
    });
    if (card.status === 'pending' || card.status === 'streaming') {
      Object.assign(card, { status: 'error', error: 'The answer was cut off. Try again.' });
    }
  } catch (err) {
    Object.assign(card, { status: 'error', error: err.message || 'The explanation failed.' });
  }
  if (S.slug === slug) { paint(card); save(); }
}

// A longer explanation, below the short one, building on it.
async function runMore(card) {
  const slug = S.slug;
  card.more = { status: 'pending', partial: '', started: Date.now() };
  card.state = 'open';
  paint(card);
  save();
  const more = card.more;
  try {
    await streamLines(`/api/papers/${slug}/explain`, {
      text: card.text, context: card.context || '', where: card.where, ...(card.who || {}),
      level: card.reading?.level || undefined, run: card.reading?.run || undefined,
      depth: 'more', brief: plainOf(card.html),
    }, (ev) => {
      if (S.slug !== slug || card.more !== more) return;
      if (ev.type === 'start') {
        Object.assign(more, { status: 'streaming', agent: ev.agent });
      } else if (ev.type === 'delta') {
        more.partial = (more.partial || '') + ev.text;
      } else if (ev.type === 'done') {
        Object.assign(more, { status: 'done', html: ev.html, agent: ev.agent,
                              seconds: ev.seconds, cost: ev.cost, cached: !!ev.cached, partial: '' });
        save();
      } else if (ev.type === 'error') {
        Object.assign(more, { status: 'error', error: ev.message });
        save();
      }
      paint(card);
    });
    if (more.status === 'pending' || more.status === 'streaming') {
      Object.assign(more, { status: 'error', error: 'The answer was cut off. Try again.' });
    }
  } catch (err) {
    Object.assign(more, { status: 'error', error: err.message || 'The explanation failed.' });
  }
  if (S.slug === slug) { paint(card); save(); }
}

// A question of the reader's own about a selection: a note of its own, like
// an explanation, with the question above its answer.
function askQuestion(sel, q) {
  if (!S.slug) return;
  const by = who();
  const reading = S.hooks.reading() || S.reading;
  const at = reading && reading.slug === S.slug
    ? { level: reading.level || null, run: reading.run || null } : null;
  const key = `${keyOf(sel.text, by?.model, at?.level)}|ask|${q.replace(/\s+/g, ' ').toLowerCase()}`;
  const same = S.cards.find(c => c.key === key && c.status !== 'error');
  if (same) { focusNote(same.uid); return; }
  const card = {
    uid: Math.random().toString(36).slice(2, 10), key, question: q,
    text: sel.text, context: sel.context, where: sel.where,
    anchor: { ...sel.anchor, reading: sel.where.kind === 'companion' ? at : null },
    state: 'open', status: 'pending', who: by, reading: at, started: Date.now(),
  };
  S.cards.unshift(card);
  S.minimized = false;
  save();
  render();
  list.scrollTo({ top: 0, behavior: 'smooth' });
  run(card);
}

// A question about the paper as a whole: a note headed by the question, with
// nothing to show in the text.
function askAnything(q) {
  if (!S.slug) return;
  const by = who();
  const reading = S.hooks.reading() || S.reading;
  const at = reading && reading.slug === S.slug
    ? { level: reading.level || null, run: reading.run || null } : null;
  const key = `${at?.level || ''}|${by?.model || ''}|whole|${q.replace(/\s+/g, ' ').toLowerCase()}`;
  const same = S.cards.find(c => c.key === key && c.status !== 'error');
  if (same) { focusNote(same.uid); return; }
  const card = {
    uid: Math.random().toString(36).slice(2, 10), key, question: q,
    text: '', context: '', where: { kind: 'whole' }, anchor: null,
    state: 'open', status: 'pending', who: by, reading: at, started: Date.now(),
  };
  S.cards.unshift(card);
  S.minimized = false;
  save();
  render();
  list.scrollTo({ top: 0, behavior: 'smooth' });
  run(card);
}

// What has been said about these words so far, for a follow-up to build on:
// the answer the note began with, the longer one, and the exchanges before
// this one — the latest few, which is all the server keeps.
function historyOf(card, upto) {
  const said = [];
  if (card.status === 'done' && card.html) {
    said.push({ q: card.question || 'What does it mean here?', a: plainOf(card.html) });
  }
  if (card.more?.status === 'done') {
    said.push({ q: 'Explain it more thoroughly.', a: plainOf(card.more.html) });
  }
  for (const t of card.thread || []) {
    if (t === upto) break;
    if (t.status === 'done') said.push({ q: t.q, a: plainOf(t.html) });
  }
  return said.slice(-4);
}

function follow(card, q) {
  card.thread = card.thread || [];
  const item = { uid: Math.random().toString(36).slice(2, 10), q, status: 'pending' };
  card.thread.push(item);
  card.following = false;
  card.state = 'open';
  paint(card);
  save();
  runFollow(card, item);
}

async function runFollow(card, item) {
  const slug = S.slug;
  Object.assign(item, { status: 'pending', error: null, partial: '', started: Date.now() });
  paint(card);
  try {
    await streamLines(`/api/papers/${slug}/explain`, {
      text: card.text, context: card.context || '', where: card.where, ...(card.who || {}),
      level: card.reading?.level || undefined, run: card.reading?.run || undefined,
      question: item.q, history: historyOf(card, item),
    }, (ev) => {
      if (S.slug !== slug || !(card.thread || []).includes(item)) return;
      if (ev.type === 'start') {
        Object.assign(item, { status: 'streaming', agent: ev.agent });
      } else if (ev.type === 'delta') {
        item.partial = (item.partial || '') + ev.text;
      } else if (ev.type === 'done') {
        Object.assign(item, { status: 'done', html: ev.html, agent: ev.agent,
                              seconds: ev.seconds, cost: ev.cost, cached: !!ev.cached, partial: '' });
        live.textContent = `Answer ready: ${item.q.slice(0, 80)}`;
        save();
      } else if (ev.type === 'error') {
        Object.assign(item, { status: 'error', error: ev.message });
        save();
      }
      paint(card);
    });
    if (item.status === 'pending' || item.status === 'streaming') {
      Object.assign(item, { status: 'error', error: 'The answer was cut off. Try again.' });
    }
  } catch (err) {
    Object.assign(item, { status: 'error', error: err.message || 'The answer failed.' });
  }
  if (S.slug === slug) { paint(card); save(); }
}

// The short explanation as text, for the longer one to build on: mathematics
// back in its dollars, page links as words.
function plainOf(html) {
  const el = document.createElement('div');
  el.innerHTML = html || '';
  el.querySelectorAll('.math').forEach(m => m.replaceWith(`$${m.textContent}$`));
  return el.textContent.replace(/\s+/g, ' ').trim().slice(0, 1500);
}

// A key term from the companion's own links, kept as a note: its short
// explanation is already there, and the longer one is asked for at once.
export function fromTerm({ text, html, where, anchor, agent, reading }) {
  if (!S.slug) return;
  const by = who();
  const at = reading ? { level: reading.level || null, run: reading.run || null } : null;
  const key = keyOf(text, by?.model, at?.level);
  let card = S.cards.find(c => c.key === key && c.status === 'done');
  if (!card) {
    card = {
      uid: Math.random().toString(36).slice(2, 10), key, text, context: '', where,
      anchor: { ...anchor, reading: at }, state: 'open', status: 'done', html, agent,
      who: by, reading: at, cached: true, fromTerm: true,
    };
    S.cards.unshift(card);
  }
  S.minimized = false;
  save();
  render();
  focusNote(card.uid);
  if (!card.more || card.more.status === 'error') runMore(card);
}

async function streamLines(url, body, onEvent) {
  const res = await authFetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let message = res.statusText;
    try { message = (await res.json()).detail || message; } catch { /* not JSON */ }
    throw new Error(message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i).trim();
      buffer = buffer.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

// ----------------------------------------------------------------- drawing

function render() {
  askButton.hidden = !S.slug;
  if (!S.slug) closeMenu();
  const show = !!S.slug && S.cards.length > 0;
  panel.hidden = !show || S.minimized;
  pill.hidden = !show || !S.minimized;
  document.body.classList.toggle('notes-open', show && !S.minimized);
  if (!show) return;
  pill.replaceChildren('Explanations ', h('span', { class: 'notes-count', text: String(S.cards.length) }));
  panel.querySelector('.notes-count').textContent = String(S.cards.length);

  const keep = new Set(S.cards.map(c => c.uid));
  for (const [uid, el] of S.els) if (!keep.has(uid)) { el.remove(); S.els.delete(uid); }
  S.cards.forEach((card, i) => {
    let el = S.els.get(card.uid);
    if (!el) { el = build(card); S.els.set(card.uid, el); paint(card); }
    if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
  });
  paintFoot();
}

function termNode(text) {
  const out = h('span', { class: 'note-term-text' });
  for (const part of text.split(/(\$\$[^$]+\$\$|\$[^$]+\$)/)) {
    if (!part) continue;
    if (part.startsWith('$$')) out.append(h('span', { class: 'math', text: part.slice(2, -2) }));
    else if (part.startsWith('$') && part.length > 2) out.append(h('span', { class: 'math', text: part.slice(1, -1) }));
    else out.append(part);
  }
  typeset(out);
  return out;
}

function placeText(where) {
  if (where?.kind === 'whole') {
    return { search: 'this search', collection: 'these papers' }[S.hooks.kind(S.slug)] || 'the paper';
  }
  if (where?.kind === 'paper' && where.page) return `page ${where.page}`;
  if (where?.kind === 'companion') return where.label || 'companion';
  if (where?.kind === 'note') return 'in a note';
  return '';
}

function build(card) {
  const whole = card.where?.kind === 'whole';
  const head = whole ? card.question : card.text;
  const term = h('button', {
    class: 'note-term', type: 'button', title: 'Open or fold this note',
    onclick: () => { card.state = card.state === 'open' ? 'folded' : 'open'; save(); paint(card); },
  }, termNode(head.length > 140 ? `${head.slice(0, 137)}…` : head));
  return h('article', { class: 'note', 'data-uid': card.uid },
    h('header', { class: 'note-head' },
      term,
      h('span', { class: 'note-where', text: placeText(card.where) }),
      h('button', { class: 'note-x', type: 'button', 'aria-label': 'Remove this note', text: '×',
                    onclick: () => { S.cards = S.cards.filter(c => c !== card); save(); render(); } })),
    card.question && !whole ? h('div', { class: 'note-q', text: card.question }) : null,
    h('div', { class: 'note-body' }),
    h('div', { class: 'note-more', hidden: true }),
    h('footer', { class: 'note-meta' }),
    // Follow-ups go at the end of the note, and so does the way to ask one.
    h('div', { class: 'note-thread', hidden: true }),
    h('div', { class: 'note-actions' }),
    h('form', { class: 'note-follow', hidden: true }));
}

function paint(card) {
  const el = S.els.get(card.uid);
  if (!el) return;
  el.className = `note ${card.state} ${card.status}`;
  el.querySelector('.note-term').setAttribute('aria-expanded', String(card.state === 'open'));
  const body = el.querySelector('.note-body');
  const meta = el.querySelector('.note-meta');
  if (card.status === 'done') {
    if (body.dataset.drawn !== 'html') {
      body.innerHTML = card.html || '';
      body.dataset.drawn = 'html';
      typeset(body);
      body.querySelectorAll('a.pageref').forEach(a => a.addEventListener('click', (ev) => {
        ev.preventDefault();
        S.hooks.openPage(Number(a.dataset.page));
      }));
    }
    const bits = [card.agent?.name || ''];
    if (card.fromTerm) bits.push('key term');
    else if (card.cached) bits.push('kept from earlier');
    else if (card.seconds != null) bits.push(`${card.seconds} s`);
    if (!card.cached && card.cost != null) bits.push(money(card.cost));
    // A question of the reader's own has its answer; "explain more" is for
    // the explanation of a term, and either can be asked a follow-up.
    const asked = card.question || (card.more && card.more.status !== 'error');
    mount(meta, h('span', { text: bits.filter(Boolean).join(' · ') }),
      asked ? null : h('button', { class: 'linkish note-more-btn', type: 'button',
                                    text: 'Explain more', onclick: () => runMore(card) }),
      card.anchor && card.anchor.kind !== 'note'
        ? h('button', { class: 'linkish', type: 'button', text: 'Show in text',
                        onclick: () => S.hooks.showInText(card.anchor) })
        : null);
    paintMore(card, el.querySelector('.note-more'));
  } else if (card.status === 'error') {
    el.querySelector('.note-more').hidden = true;
    card.following = false;
    body.dataset.drawn = '';
    mount(body, h('p', { class: 'note-error', text: card.error || 'The explanation failed.' }));
    mount(meta, h('button', { class: 'linkish', type: 'button', text: 'Try again',
                              onclick: () => run(card) }));
  } else {
    body.dataset.drawn = '';
    if (card.partial) {
      mount(body, h('p', { class: 'note-partial', text: card.partial }));
    } else {
      mount(body, h('p', { class: 'note-wait' }, h('span', { class: 'spin' }),
                   h('span', { class: 'note-wait-text', text: 'Explaining…' })));
    }
    mount(meta, card.agent ? h('span', { text: card.agent.name }) : null);
  }
  paintThread(card, el);
}

// Follow-ups, under the note they follow: each question, and its answer as
// it arrives. A finished answer is drawn once, so that its mathematics is not
// typeset again every time another part of the note changes.
function paintThread(card, el) {
  const box = el.querySelector('.note-thread');
  const items = card.thread || [];
  box.hidden = !items.length;
  const keep = new Set(items.map(t => t.uid));
  [...box.children].forEach(c => { if (!keep.has(c.dataset.uid)) c.remove(); });
  items.forEach((t, i) => {
    let row = box.querySelector(`[data-uid="${t.uid}"]`);
    if (!row) {
      row = h('div', { class: 'note-qa', 'data-uid': t.uid },
        h('div', { class: 'note-qa-q', text: t.q }), h('div', { class: 'note-qa-a' }));
    }
    if (box.children[i] !== row) box.insertBefore(row, box.children[i] || null);
    const answer = row.querySelector('.note-qa-a');
    if (t.status === 'done') {
      if (answer.dataset.drawn === 'html') return;
      const bits = [t.cached ? 'kept from earlier' : t.seconds != null ? `${t.seconds} s` : '',
                    !t.cached && t.cost != null ? money(t.cost) : ''].filter(Boolean).join(' · ');
      mount(answer, h('div', { class: 'note-qa-body', html: t.html || '' }),
        bits ? h('div', { class: 'note-qa-meta', text: bits }) : null);
      answer.dataset.drawn = 'html';
      typeset(answer);
      answer.querySelectorAll('a.pageref').forEach(a => a.addEventListener('click', (ev) => {
        ev.preventDefault();
        S.hooks.openPage(Number(a.dataset.page));
      }));
      return;
    }
    answer.dataset.drawn = '';
    if (t.status === 'error') {
      mount(answer, h('p', { class: 'note-error', text: t.error || 'The answer failed.' }),
        h('button', { class: 'linkish', type: 'button', text: 'Try again',
                      onclick: () => runFollow(card, t) }));
    } else if (t.partial) {
      mount(answer, h('p', { class: 'note-partial', text: t.partial }));
    } else {
      mount(answer, h('p', { class: 'note-wait' }, h('span', { class: 'spin' }),
                         h('span', { class: 'note-wait-text follow', 'data-uid': t.uid,
                                     text: 'Answering…' })));
    }
  });

  const actions = el.querySelector('.note-actions');
  const open = card.status === 'done' && !card.following;
  if (actions.childElementCount !== (open ? 1 : 0)) {
    mount(actions, open ? h('button', {
      class: 'linkish note-follow-btn', type: 'button', text: 'Ask a follow-up',
      onclick: () => {
        card.following = true;
        paint(card);
        el.querySelector('.note-follow textarea')?.focus();
      },
    }) : null);
  }

  const form = el.querySelector('.note-follow');
  form.hidden = !card.following || card.status !== 'done';
  if (form.hidden || form.childElementCount) return;
  const field = h('textarea', { rows: '2', maxlength: '600', 'aria-label': 'Your follow-up question',
                                placeholder: card.where?.kind === 'whole' ? 'Ask a follow-up…'
                                  : 'Ask a follow-up about the same words…' });
  const submit = () => {
    const q = field.value.trim();
    if (!q) { field.focus(); return; }
    field.value = '';
    follow(card, q);
  };
  field.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    if (e.key === 'Escape') { e.preventDefault(); card.following = false; paint(card); }
  });
  form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
  mount(form, field, h('div', { class: 'note-follow-foot' },
    h('button', { class: 'linkish', type: 'button', text: 'Cancel',
                  onclick: () => { card.following = false; paint(card); } }),
    h('button', { class: 'primary ask-go', type: 'submit', text: 'Ask' })));
}

function paintMore(card, box) {
  const more = card.more;
  box.hidden = !more;
  if (!more) return;
  if (more.status === 'done') {
    if (box.dataset.drawn === 'html') return;
    const meta = [more.cached ? 'kept from earlier' : more.seconds != null ? `${more.seconds} s` : '',
                  !more.cached && more.cost != null ? money(more.cost) : ''].filter(Boolean).join(' · ');
    mount(box, h('div', { class: 'note-more-label', text: 'More' }),
      h('div', { class: 'note-more-body', html: more.html || '' }),
      meta ? h('div', { class: 'note-more-meta', text: meta }) : null);
    box.dataset.drawn = 'html';
    typeset(box);
    box.querySelectorAll('a.pageref').forEach(a => a.addEventListener('click', (ev) => {
      ev.preventDefault();
      S.hooks.openPage(Number(a.dataset.page));
    }));
    return;
  }
  box.dataset.drawn = '';
  if (more.status === 'error') {
    mount(box, h('div', { class: 'note-more-label', text: 'More' }),
      h('p', { class: 'note-error', text: more.error || 'The explanation failed.' }),
      h('button', { class: 'linkish', type: 'button', text: 'Try again', onclick: () => runMore(card) }));
    return;
  }
  mount(box, h('div', { class: 'note-more-label', text: 'More' }),
    more.partial
      ? h('p', { class: 'note-partial', text: more.partial })
      : h('p', { class: 'note-wait' }, h('span', { class: 'spin' }),
             h('span', { class: 'note-wait-text more', text: 'Explaining more…' })));
}

// The wait is counted aloud only once it is long enough to wonder about.
function tick() {
  for (const card of S.cards) {
    if (card.status === 'pending') {
      const el = S.els.get(card.uid)?.querySelector('.note-body .note-wait-text');
      const s = Math.round((Date.now() - (card.started || Date.now())) / 1000);
      if (el) el.textContent = s >= 4 ? `Explaining… ${s} s` : 'Explaining…';
    }
    if (card.more?.status === 'pending') {
      const el = S.els.get(card.uid)?.querySelector('.note-wait-text.more');
      const s = Math.round((Date.now() - (card.more.started || Date.now())) / 1000);
      if (el) el.textContent = s >= 4 ? `Explaining more… ${s} s` : 'Explaining more…';
    }
    for (const t of card.thread || []) {
      if (t.status !== 'pending') continue;
      const el = S.els.get(card.uid)?.querySelector(`.note-wait-text.follow[data-uid="${t.uid}"]`);
      const s = Math.round((Date.now() - (t.started || Date.now())) / 1000);
      if (el) el.textContent = s >= 4 ? `Answering… ${s} s` : 'Answering…';
    }
  }
}

function paintFoot() {
  const groups = (S.choices || []).map(g => ({ ...g, models: [...g.models] }));
  if (!groups.length) { mount(foot); return; }
  const current = who();
  if (current?.custom) {
    const g = groups.find(x => x.account === current.account);
    if (g && !g.models.some(m => m.id === current.model)) {
      g.models.push({ id: current.model, name: current.model, custom: true });
    }
  }
  const sel = h('select', { 'aria-label': 'AI for explanations' },
    groups.map((g, gi) => h('optgroup', { label: g.label },
      g.models.map((m, mi) => h('option', { value: `${gi}:${mi}`, text: m.name })))));
  const gi = groups.findIndex(g => g.account === current?.account);
  const mi = gi >= 0 ? groups[gi].models.findIndex(m => m.id === current?.model) : -1;
  if (gi >= 0 && mi >= 0) sel.value = `${gi}:${mi}`;
  sel.addEventListener('change', () => {
    const [g, m] = sel.value.split(':').map(Number);
    const model = groups[g].models[m];
    settings.set({ explainer: { account: groups[g].account, model: model.id,
                                ...(model.custom ? { custom: true } : {}) } },
                 'notes');
  });
  mount(foot, h('span', { text: 'For explanations' }), sel);
}

function money(usd) {
  return usd < 0.01 ? `$${usd.toFixed(3)}` : `$${usd.toFixed(2)}`;
}

// --------------------------------------------------------------- finding

// A range over the first place these words appear under `root`, comparing
// only the characters that are not white space: line breaks, a PDF's gaps
// and KaTeX's spacing all differ from one drawing of the text to the next.
function locate(root, find) {
  const want = find.replace(/\s+/g, '');
  if (!want) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let hay = '';
  const map = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.nodeValue;
    for (let i = 0; i < t.length; i++) {
      if (/\s/.test(t[i])) continue;
      hay += t[i];
      map.push([n, i]);
    }
  }
  const at = hay.indexOf(want);
  if (at < 0) return null;
  const [sn, so] = map[at];
  const [en, eo] = map[at + want.length - 1];
  const range = document.createRange();
  range.setStart(sn, so);
  range.setEnd(en, eo + 1);
  return range;
}

// ----------------------------------------------------------------- storage

function stored(store, key) {
  try { return JSON.parse(store.getItem(key) || 'null'); } catch { return null; }
}

function save() {
  if (!S.slug) return;
  const cards = S.cards.map(c => ({
    uid: c.uid, key: c.key, text: c.text, context: c.context, where: c.where,
    anchor: c.anchor, state: c.state, who: c.who, reading: c.reading,
    // An answer that was still arriving is asked for again, not kept half-said.
    status: c.status === 'streaming' ? 'pending' : c.status,
    html: c.html, agent: c.agent, seconds: c.seconds, cost: c.cost,
    cached: c.cached, error: c.error, fromTerm: c.fromTerm, question: c.question,
    more: c.more ? { ...c.more, status: c.more.status === 'streaming' ? 'pending' : c.more.status,
                     partial: '' } : null,
    thread: (c.thread || []).map(t => ({ ...t, partial: '',
                                         status: t.status === 'streaming' ? 'pending' : t.status })),
  }));
  try { sessionStorage.setItem(SESSION(S.slug), JSON.stringify({ cards, minimized: S.minimized })); }
  catch { /* a private window, or storage off: the notes last as long as the page */ }
}

async function getJSON(url) {
  try {
    const res = await authFetch(url);
    return res.ok ? await res.json() : null;
  } catch { return null; }
}
