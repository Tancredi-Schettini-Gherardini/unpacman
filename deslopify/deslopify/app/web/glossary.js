/* Key terms, linked in the companion, Wikipedia style.
 *
 * Once per reading the AI that explains is asked for the terms its companion
 * most needs, each with a short explanation that keeps every rule an answer
 * keeps (the paper's own definition and its page, or a plain statement that
 * the paper gives none). The first mention of each term in each section
 * becomes a link; pointing at it shows a small preview card, as Wikipedia's
 * page previews do, and "Explain more" turns it into a note with a longer
 * explanation. The links appear when the terms arrive; the companion is
 * readable at once and never waits for them. */

import { authFetch, h, mount, typeset } from './dom.js';
import * as notes from './notes.js';
import * as settings from './settings.js';

const SHOW_AFTER = 300;     // ms of pointing before a card opens
const HIDE_AFTER = 220;     // ms of grace to move into it
// Never inside these: a heading, mathematics, code, another link, the
// bibliography, the check's findings.
const SKIP = 'a, .katex, .math, code, pre, h1, h2, h3, h4, h5, h6, .bibentry, .finding';
const NOT_HERE = new Set(['s-references', 's-review', 's-map']);

let card = null;
let terms = [];             // [{term, html}] for the companion on screen
let meta = null;            // {agent, reading, slug}
let openFor = null;         // the link whose card is showing
let pinned = false;
let showTimer = 0;
let hideTimer = 0;

export function init({ openPage }) {
  card = h('div', { class: 'preview', id: 'term-preview', role: 'tooltip', hidden: true });
  document.body.append(card);

  card.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  card.addEventListener('pointerleave', () => { if (!pinned) scheduleHide(); });
  card.addEventListener('click', (e) => {
    const page = e.target.closest('a.pageref');
    if (page) { e.preventDefault(); hide(); openPage(Number(page.dataset.page)); }
  });

  document.addEventListener('pointerover', (e) => {
    const link = e.target.closest?.('a.term');
    if (!link || e.pointerType === 'touch') return;
    clearTimeout(hideTimer);
    if (openFor === link) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => show(link), pinned ? 0 : SHOW_AFTER);
  });
  document.addEventListener('pointerout', (e) => {
    const link = e.target.closest?.('a.term');
    if (!link || e.pointerType === 'touch') return;
    clearTimeout(showTimer);
    if (!pinned && !card.contains(e.relatedTarget)) scheduleHide();
  });
  // A click, or a tap — touch has no hover — keeps the card open until the
  // reader clicks elsewhere; following the link would go nowhere.
  document.addEventListener('click', (e) => {
    const link = e.target.closest?.('a.term');
    if (link) {
      e.preventDefault();
      if (openFor === link && pinned) { hide(); return; }
      show(link);
      pinned = true;
      return;
    }
    if (!card.hidden && !card.contains(e.target)) hide();
  });
  document.addEventListener('focusin', (e) => {
    const link = e.target.closest?.('a.term');
    if (link) show(link);
    else if (!card.contains(e.target) && !pinned) hide();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !card.hidden) hide(); });
  // The window's scrolling, or a pane's in the side-by-side view.
  document.addEventListener('scroll', () => { if (!card.hidden) (pinned ? place(openFor) : hide()); },
                            { capture: true, passive: true });
  window.addEventListener('resize', () => hide());
}

function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hide, HIDE_AFTER);
}

function hide() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  if (card) card.hidden = true;
  openFor?.removeAttribute('aria-describedby');
  openFor = null;
  pinned = false;
}

function place(link) {
  if (!link?.isConnected) { hide(); return; }
  const r = link.getBoundingClientRect();
  const w = card.offsetWidth;
  const ht = card.offsetHeight;
  let top = r.bottom + 8;
  if (top + ht > window.innerHeight - 8 && r.top - ht - 8 > 8) top = r.top - ht - 8;
  card.style.top = `${Math.max(8, top)}px`;
  card.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
}

function show(link) {
  const t = terms[Number(link.dataset.term)];
  if (!t) return;
  clearTimeout(hideTimer);
  if (openFor && openFor !== link) openFor.removeAttribute('aria-describedby');
  openFor = link;
  link.setAttribute('aria-describedby', 'term-preview');
  const sec = link.closest('section.sec');
  mount(card,
    h('div', { class: 'preview-term', text: t.term }),
    h('div', { class: 'preview-body', html: t.html }),
    h('div', { class: 'preview-foot' },
      h('span', { text: meta?.agent?.name ? `Key term · ${meta.agent.name}` : 'Key term' }),
      h('button', {
        class: 'linkish', type: 'button', text: 'Explain more',
        onclick: () => {
          hide();
          notes.fromTerm({
            text: t.term, html: t.html, agent: meta?.agent, reading: meta?.reading,
            where: { kind: 'companion', section: sec?.id?.replace(/^s-/, ''),
                     label: sec?.querySelector('h2')?.firstChild?.textContent?.trim() },
            anchor: { kind: 'companion', section: sec?.id?.replace(/^s-/, ''),
                      find: link.textContent },
          });
        },
      })));
  typeset(card);
  card.hidden = false;
  place(link);
}

// --------------------------------------------------------------- linking

function patternFor(term) {
  const words = term.split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const body = `${words.join('\\s+')}(?:s|es)?`;
  try { return new RegExp(`(?<![\\p{L}\\p{N}])${body}(?![\\p{L}\\p{N}])`, 'iu'); }
  catch { return new RegExp(`\\b${body}\\b`, 'i'); }   // a browser without lookbehind
}

function linkFirst(body, t) {
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest(SKIP)
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const m = t.re.exec(n.nodeValue);
    if (!m) continue;
    const hit = n.splitText(m.index);
    hit.splitText(m[0].length);
    hit.replaceWith(h('a', { class: 'term', href: '#', 'data-term': String(t.i),
                             text: hit.nodeValue }));
    return true;
  }
  return false;
}

// The first mention in each section — a reader who arrives at a section from
// the contents meets its terms linked, as one arriving at a Wikipedia article
// does — longest terms first, so "exotic sphere" wins over "sphere".
function apply(root) {
  const ordered = terms.map((t, i) => ({ ...t, i, re: patternFor(t.term) }))
    .sort((a, b) => b.term.length - a.term.length);
  const linked = new Set();
  for (const sec of root.querySelectorAll('section.sec')) {
    if (NOT_HERE.has(sec.id)) continue;
    const body = sec.querySelector('.body');
    if (!body) continue;
    for (const t of ordered) if (linkFirst(body, t)) linked.add(t.i);
  }
  return linked.size;
}

export function unlink(root) {
  hide();
  root.querySelectorAll('a.term').forEach(a => a.replaceWith(a.textContent));
  root.normalize();
}

// Ask for the terms of the reading on screen and link them. `say` reports
// progress in a sentence, for the rail.
export async function link(root, slug, reading, say = () => {}) {
  if (!settings.get().linkTerms) { say(''); return; }
  say('Linking the key terms…', 'busy');
  // The explaining AI chosen in Advanced settings, while the menu still offers
  // it; else the server's own choice (the AI that wrote the companion).
  const menu = await settings.menu();
  const explainer = settings.offeredPick(settings.get().explainer, menu);
  let res;
  try {
    const r = await authFetch(`/api/papers/${slug}/glossary`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level: reading?.level, run: reading?.run, ...explainer,
                             effort: settings.explanationEffort(menu?.groups, explainer || undefined) }),
    });
    res = await r.json();
    if (!r.ok) res = { status: 'error', message: res?.detail || r.statusText };
  } catch (err) {
    res = { status: 'error', message: err.message };
  }
  if (!root.isConnected) return;               // the reader has moved on
  if (res.status !== 'ready') {
    say(`The key terms could not be linked: ${res.message || 'no answer came back'}.`, 'error',
        () => link(root, slug, reading, say));
    return;
  }
  terms = res.terms || [];
  meta = { agent: res.agent, reading, slug };
  unlink(root);
  const n = apply(root);
  say(n ? `${n} key term${n === 1 ? ' is' : 's are'} linked — point at one for a short explanation.`
        : 'No key terms could be found in this companion.', 'done');
}
