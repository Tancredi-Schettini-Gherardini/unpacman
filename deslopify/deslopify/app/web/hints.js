/* Tips, the first time each part of the page is met.
 *
 * A small card beside one thing at a time — the drop box, the level, the
 * Start button, the divider — saying what it does, with "Next" to go on and
 * "Skip tips" to stop them all. Nothing is dimmed and nothing is blocked: the
 * page can be used with a tip on screen, and using it elsewhere does not lose
 * the tip. Each screen's tips are shown once in this browser; the "?" in the
 * bar shows the current screen's again, and Advanced settings can bring them
 * all back. */

import { h } from './dom.js';

const KEY = 'deslopify.hints';
const TOUCH = matchMedia('(hover: none) and (pointer: coarse)').matches;

// For each screen, in the order a newcomer meets them. A tip whose target is
// not on the page, or not in view, is passed over.
const TIPS = {
  home: [
    { at: '.ready-pill', title: 'Getting ready',
      text: 'What this computer still needs before a first reading — an AI to read with, '
            + 'and LaTeX. Each takes one click there.' },
    { at: '#drop', title: 'Start here',
      text: 'Drop a paper’s PDF anywhere on this page, or click here to choose one — or '
            + 'several at once, to have them read together.' },
    { at: '#arxiv', title: 'By its arXiv number',
      text: 'Or type a paper’s arXiv number — 2309.01234, or math/0601001 — and unpacman '
            + 'fetches it from arXiv, as if you had dropped its PDF. Several numbers are read '
            + 'together.' },
    { at: '#find', title: 'Find papers',
      text: 'Ask for papers on a subject — the latest arXiv preprints in a field, or who has '
            + 'written on a question. Any paper it finds can be added here and read.' },
    { at: '#open-settings', title: 'Advanced settings',
      text: 'Which AI reads and explains, the limits on every reading, updates and Quit — '
            + 'and the method itself: every instruction the AI follows, which you can read '
            + 'and change.' },
    { at: '#open-feedback', title: 'Feedback',
      text: 'Something not working, or an idea? This writes an email to the people who make '
            + 'unpacman, with a report on this copy if you like. Nothing is sent until you '
            + 'send it.' },
    { at: '#library .row', title: 'Your papers',
      text: 'Every paper you have given it. Open one to see its readings, or to read it again.' },
  ],
  launch: [
    { at: '#drop-more', title: 'More papers',
      text: 'Another paper can join this one before anything starts: drop its PDF here or '
            + 'anywhere on the page, click to choose it, or give its arXiv number just below. '
            + 'The papers are then read together.' },
    { at: '.choices', title: 'Who is it for?',
      text: 'Expert keeps the technical detail; Non-expert explains the ideas and what '
            + 'they rest on. Choosing one starts nothing yet.' },
    { at: '.launch-ai', title: 'Which AI, and its limits',
      text: 'The model that reads, how hard it thinks, and the money and time the '
            + 'reading may use. It stops at whichever limit it reaches first.' },
    { at: '.reading-addon', title: 'A diagram',
      text: 'For pure math papers, the writer can add a one-page diagram of the proof.' },
    { at: '#start-reading', title: 'Start',
      text: 'Nothing is spent until you press this. Once it has started you can close '
            + 'the page: the reading carries on.' },
  ],
  reading: [
    { at: '#r-steps', title: 'The reading, step by step',
      text: 'Each step appears here as it happens, with what it found. The companion opens '
            + 'by itself when the reading is done.' },
  ],
  companion: [
    { at: '.switch', title: 'Companion, paper, or both',
      text: 'Move between this companion and the paper itself — or put the two side by side.' },
    { at: '#paper .sec .body p', title: 'Ask about anything', about: 'ask',
      text: TOUCH
        ? 'Select a word or a passage — here or in the paper — then tap Explain, or Ask… '
          + 'to put your own question. Answers stay beside the text.'
        : 'Select a word or a passage — here or in the paper — and right-click: Explain '
          + 'it, or ask your own question. Answers stay beside the text.' },
    { at: '#paper a.term', title: 'Key terms',
      text: 'Terms in blue are explained: point at one for a short explanation.' },
    { at: '#rail', title: 'Contents and files',
      text: 'Jump to any section. Below the contents: the paper, and this companion as a PDF.' },
  ],
  side: [
    { at: '.side-split', title: 'The divider',
      text: 'Drag it to give either side more room; double-click it to centre it again.' },
    { at: '.side-bar', title: 'The paper',
      text: 'Page links in explanations move this side to the page, without leaving the view.' },
  ],
  paper: [
    { at: '.viewer-body', title: 'The paper itself', about: 'ask',
      text: TOUCH ? 'Select any passage, then tap Explain or Ask… about it.'
        : 'Select any passage and right-click it to have it explained, or to ask about it.' },
  ],
  runs: [
    { at: '.run-new', title: 'Read it again',
      text: 'Start another reading of this paper: at the other level, or with another AI.' },
  ],
};

let card = null;
let ring = null;               // the element a tip is about
// What something else on the page already says, so that no tip says it again:
// "ask", while the first run's suggestion is on screen (firstask.js).
const covered = new Set();

export function cover(topic, on) {
  if (on) covered.add(topic);
  else covered.delete(topic);
}
let screen = null;
let steps = [];
let index = 0;

function stored() {
  try { return JSON.parse(localStorage.getItem(KEY) || 'null') || { seen: {} }; }
  catch { return { seen: {} }; }
}

function store(data) {
  try { localStorage.setItem(KEY, JSON.stringify(data)); } catch { /* not kept */ }
}

function visible(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight
    && r.right > 0 && r.left < window.innerWidth;
}

// Show a screen's tips, the first time it is met — or again, when asked.
export function show(name, { again = false } = {}) {
  hide();
  const data = stored();
  if (!TIPS[name] || (!again && (data.off || data.seen?.[name]))) return;
  // After the screen has settled: a target drawn a moment later still counts.
  setTimeout(() => {
    steps = TIPS[name].filter(t => !(t.about && covered.has(t.about))
                                   && visible(document.querySelector(t.at)));
    if (!steps.length) return;
    screen = name;
    index = 0;
    const now = stored();
    now.seen = { ...(now.seen || {}), [name]: true };
    if (again) now.off = false;
    store(now);
    paint();
  }, again ? 0 : 700);
}

export function hide() {
  card?.remove();
  card = null;
  ring?.classList.remove('hint-target');
  ring = null;
  screen = null;
}

// Every tip, on every screen, the next time it is met.
export function reset() {
  store({ seen: {} });
}

function paint() {
  const tip = steps[index];
  const target = tip && document.querySelector(tip.at);
  if (!target) { hide(); return; }
  ring?.classList.remove('hint-target');
  ring = target;
  ring.classList.add('hint-target');
  const last = index === steps.length - 1;
  card?.remove();
  card = h('div', { class: 'hint', role: 'dialog', 'aria-labelledby': 'hint-title' },
    h('div', { class: 'hint-arrow' }),
    h('div', { class: 'hint-title', id: 'hint-title', text: tip.title }),
    h('p', { class: 'hint-text', text: tip.text }),
    h('div', { class: 'hint-foot' },
      h('span', { class: 'hint-count', text: steps.length > 1 ? `${index + 1} of ${steps.length}` : '' }),
      h('button', { class: 'linkish', type: 'button', text: 'Skip tips',
                    onclick: () => { const d = stored(); d.off = true; store(d); hide(); } }),
      h('button', { class: 'primary hint-next', type: 'button', text: last ? 'Got it' : 'Next',
                    onclick: () => { if (last) hide(); else { index += 1; paint(); } } })));
  document.body.append(card);
  place();
}

// Beside its target: below it if there is room, else above; always on screen.
function place() {
  if (!card || !ring) return;
  if (!ring.isConnected) { hide(); return; }
  const r = ring.getBoundingClientRect();
  const w = card.offsetWidth;
  const ht = card.offsetHeight;
  const gap = 12;
  const below = r.bottom + gap + ht <= window.innerHeight - 8 || r.top - gap - ht < 8;
  const top = below ? Math.min(r.bottom + gap, window.innerHeight - ht - 8) : r.top - gap - ht;
  const left = Math.max(8, Math.min(r.left + Math.min(24, r.width / 2) - 20, window.innerWidth - w - 8));
  card.style.top = `${Math.max(8, top)}px`;
  card.style.left = `${left}px`;
  card.classList.toggle('above', !below);
  const arrow = Math.max(14, Math.min(w - 22, r.left + Math.min(r.width / 2, 40) - left));
  card.style.setProperty('--arrow', `${arrow}px`);
}

document.addEventListener('scroll', place, { capture: true, passive: true });
window.addEventListener('resize', place);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && card) hide();
});

// The "?" in the bar: this screen's tips, again.
export function button(name) {
  return h('button', {
    class: 'ghost hint-again', type: 'button', text: '?', id: 'hint-again',
    'aria-label': 'Show the tips for this page', title: 'Tips for this page',
    onclick: () => show(name, { again: true }),
  });
}
