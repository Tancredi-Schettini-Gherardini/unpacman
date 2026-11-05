/* The first run's suggestion (Tancredi, 2026-10-20): highlight some text,
 * right-click it, and ask about it — in the companion, or in the paper's PDF.
 * Since 2026-10-23 it appears once the first reading has started, while it
 * runs: the paper can be read, and asked about, before its companion exists.
 *
 * Shown only to someone new to unpacman (firstask.py decides, once), in the
 * views where it can be done: the companion, side by side, and the paper —
 * and on the reading's page while it runs, with the way to the paper. It
 * has no close button and says why: it stays until a selection has been
 * asked about, here or in the paper, and is then gone for good. It sits in the
 * corner (corner.js), above the button to ask a question of one's own, and
 * beneath the right-click menu, the toasts and the tips. */

import { corner } from './corner.js';
import { api, h } from './dom.js';
import * as hints from './hints.js';

const TOUCH = matchMedia('(hover: none) and (pointer: coarse)').matches;
// What to do, said where it is to be done: `inIt` names the place, or not.
const ACT = TOUCH
  ? (inIt) => `select a word or a passage${inIt}, then tap Explain — or Ask… to put a question `
    + 'of your own.'
  : (inIt) => `highlight a word or a passage${inIt}, right-click it, and ask about it: Explain `
    + 'it, or put a question of your own.';
const ALSO = {
  companion: 'The same works in the paper’s PDF, not only in this companion.',
  side: 'It works on both sides: in this companion, and in the paper’s PDF.',
  paper: 'The same works in the companion, not only in this PDF.',
};
const NO_WAIT = 'There is no need to wait for the companion.';

let pending = false;
let started = false;       // a reading has begun since this copy was new
let where = null;          // 'reading' | 'members' | 'companion' | 'side' | 'paper' | null
let about = {};            // the view's own: { slug, kind, running, companion }
let box = null;

// Asked once, when the page starts.
export async function load() {
  try {
    const now = await api('GET', '/api/first-ask');
    pending = now?.pending === true;
    started = now?.started === true;
  } catch { pending = false; }
  hints.cover('ask', pending && started);
  draw();
}

// A reading has just been started from this page (app.js): the box shows from
// now on, without asking the server again.
export function begin() {
  if (!pending || started) return;
  started = true;
  hints.cover('ask', true);
  draw();
}

// The view now on screen, if it is one where text can be asked about — or
// the reading's page, while it runs.
export function at(view, opts = {}) {
  where = view;
  about = opts;
  draw();
}

// Between views (app.js route): the box stays on screen while the next view
// is drawn, and goes at the end only if that view did not ask for it.
export function leaving() { where = null; }
export function settle() { draw(); }

export function asked() {
  if (!pending) return;
  pending = false;
  hints.cover('ask', false);
  const gone = box;
  box = null;
  if (gone) {
    gone.classList.add('done');
    setTimeout(() => gone.remove(), 400);
  }
}

// The box's words for the view on screen, or null where it is not shown.
function words() {
  const cap = (s) => s[0].toUpperCase() + s.slice(1);
  if (where === 'reading') {
    if (!about.running) return null;
    const many = about.kind === 'collection';
    return { title: 'Start reading while you wait',
             text: `Open the ${many ? 'papers' : 'paper'}, ${ACT(many ? ' in one' : ' in it')} ${NO_WAIT}`,
             go: { href: `#/p/${about.slug}/paper`, text: many ? 'Open the papers' : 'Open the paper' } };
  }
  if (where === 'members') {
    return { title: about.running ? 'Start reading while you wait' : 'Ask about anything you read',
             text: `Open one of these papers, ${ACT(' in it')}${about.running ? ` ${NO_WAIT}` : ''}` };
  }
  if (where === 'paper' && about.running) {
    return { title: 'Start reading while you wait', text: `${cap(ACT(' in the PDF'))} ${NO_WAIT}` };
  }
  if (where === 'paper' && !about.companion) {
    return { title: 'Ask about anything you read', text: cap(ACT(' in the PDF')) };
  }
  if (where === 'companion' || where === 'side' || where === 'paper') {
    return { title: 'Ask about anything you read', text: `${cap(ACT(''))} ${ALSO[where]}` };
  }
  return null;
}

function draw() {
  const say = pending && started ? words() : null;
  if (!say) {
    box?.remove();
    box = null;
    return;
  }
  if (!box) {
    box = h('aside', { class: 'first-ask', role: 'note', 'aria-labelledby': 'first-ask-title' },
      h('div', { class: 'first-ask-title', id: 'first-ask-title' }),
      h('p', { class: 'first-ask-text' }),
      h('a', { class: 'primary first-ask-go', hidden: true }),
      h('p', { class: 'first-ask-foot', text: 'This stays until you have asked once.' }));
    corner().append(box);
  }
  box.querySelector('.first-ask-title').textContent = say.title;
  box.querySelector('.first-ask-text').textContent = say.text;
  const go = box.querySelector('.first-ask-go');
  go.hidden = !say.go;
  if (say.go) {
    go.href = say.go.href;
    go.textContent = say.go.text;
  }
}

// Sent by notes.js whenever a selection is asked about.
window.addEventListener('unpacman:asked', asked);
