/* Before a Non-expert reading: what the reader already knows (questions.py).
 *
 * The questions come from the paper — what it assumes — and are asked of the
 * reader once, before anything is written. "Yes", "Roughly" or "No" to each;
 * a "No" can bring a follow-up one step down ("…and a smooth manifold?"),
 * shown at once beneath it. The answers go with the reading, and every pass of
 * it is told them. Skipping is always possible: the reading then starts as it
 * always did, for "a mathematician from outside the field".
 *
 * Several papers read together are asked about in the same way, before their
 * Non-expert collection companion: one set of questions for the whole set. */

import { api, h, mathText, mount, toast, typeset } from './dom.js';

const KEEP = 'unpacman.answers';
const CHOICES = [['yes', 'Yes'], ['roughly', 'Roughly'], ['no', 'No']];
const KINDS = {
  definition: 'Definition', theorem: 'Theorem', argument: 'Argument',
  technique: 'Technique', object: 'Object', notation: 'Notation',
};

// Answers being given, kept for the session: a reload, or a look at the paper
// and back, does not lose them.
function kept(slug, key) {
  try { return JSON.parse(sessionStorage.getItem(`${KEEP}.${slug}.${key}`) || 'null') || null; }
  catch { return null; }
}
function keep(slug, key, answers) {
  try { sessionStorage.setItem(`${KEEP}.${slug}.${key}`, JSON.stringify(answers)); }
  catch { /* private browsing: kept for as long as the page */ }
}

// The questions on screen: every first question, and a follow-up only when
// its parent's answer calls for it (questions.asked, the same rule).
function shown(questions, answers) {
  const visible = new Set();
  return questions.filter((q) => {
    const ok = !q.parent || (visible.has(q.parent) && (q.ask_if || []).includes(answers[q.parent]));
    if (ok) visible.add(q.id);
    return ok;
  });
}

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

// The questionnaire's beginning, brought into view if the page is scrolled
// below it. Continue sits at the foot of the launch panel, and the questions
// used to arrive wherever it had been — on a phone, from the fourth question
// on. A page that already shows the beginning is not moved.
function showBeginning(el) {
  const top = el.getBoundingClientRect?.().top;
  if (top === undefined) return;
  const bar = document.querySelector('.bar')?.offsetHeight || 0;
  if (top < bar) el.scrollIntoView({ block: 'start' });
}

/* `start(profile)` starts the reading (profile null: without answers) and
 * resolves once it has; `back()` returns to the choice of level. */
export async function askFirst(box, { paper, pick, start, back }) {
  // Several papers read together are asked about as one.
  const many = paper.kind === 'collection';
  const head = [
    h('div', { class: 'chosen-name', text: paper.title }),
    h('h2', { class: 'qs-title', text: 'What do you already know?' }),
    h('p', { class: 'qs-intro', text:
      'So that the companion explains what you need and skips what you know, answer a few '
      + `questions about what ${many ? 'these papers assume' : 'this paper assumes'}. `
      + 'There are no wrong answers: “No” only means it will explain more.' }),
  ];
  const request = new AbortController();
  let starting = false;
  let questionsReady = false;
  const skip = h('button', { class: 'linkish', type: 'button', text: 'Skip the questions and start' });
  // The container can be reused for the level picker or a newer questionnaire.
  // Being connected alone does not mean this questionnaire is still on screen.
  const current = () => box.isConnected && box.contains(skip);
  const stopQuestions = () => { clearInterval(tick); request.abort(); };
  const backButton = h('button', { class: 'linkish', type: 'button', text: 'Back',
    onclick: () => { if (!starting) { stopQuestions(); back(); } } });

  const showFailure = (err) => {
    const retry = h('button', { class: 'ghost', type: 'button', text: 'Try again',
                                onclick: () => askFirst(box, { paper, pick, start, back }) });
    mount(box, head,
      h('div', { class: 'notice warn', role: 'alert', text: err.message }),
      h('div', { class: 'chosen-foot' }, backButton, retry, skip));
    showBeginning(head[0]);
  };

  // Starting, from either button: the buttons wait, and come back on failure.
  const begin = async (button, profile) => {
    if (starting || button.disabled || !current()) return;
    starting = true;
    // Stop waiting in the browser, not the shared server-side generation: it
    // may still finish and cache questions for a later visit. Neither a late
    // success nor a late failure may redraw this screen after Skip.
    stopQuestions();
    const controls = [...box.querySelectorAll('button, input')];
    controls.forEach(c => { c.dataset.was = c.disabled ? '1' : ''; c.disabled = true; });
    const was = button.textContent;
    mount(button, h('span', { class: 'spin' }), ' Starting…');
    const waiting = box.querySelector('.qs-wait');
    if (waiting) mount(waiting, h('span', { class: 'spin' }), ' Starting the reading without questions…');
    const note = box.querySelector('.qs-note');
    if (note) note.textContent = 'The run will use the standard non-expert instructions.';
    try {
      await start(profile);
    } catch (err) {
      if (!current()) return;
      starting = false;
      button.textContent = was;
      controls.forEach(c => { c.disabled = c.dataset.was === '1'; });
      if (questionsReady) toast(err.message, 'err');
      else showFailure(err); // No question request remains to finish the loading screen.
    }
  };
  skip.addEventListener('click', () => begin(skip, null));

  // --- while the questions are made ---------------------------------------
  const since = Date.now();
  const clock = h('span', { class: 'qs-clock', text: '' });
  mount(box, head,
    h('p', { class: 'qs-wait', role: 'status' }, h('span', { class: 'spin' }),
      many ? ' Reading the papers to see what they assume… '
        : ' Reading the paper to see what it assumes… ', clock),
    h('p', { class: 'qs-note', text: 'This takes up to a minute or two the first time; '
             + 'the questions are kept, so next time they are here at once.' }),
    h('div', { class: 'chosen-foot' }, backButton, skip));
  showBeginning(head[0]);
  const tick = setInterval(() => {
    if (!current()) { stopQuestions(); return; }
    clock.textContent = duration((Date.now() - since) / 1000);
  }, 1000);

  let made;
  try {
    made = await api('POST', `/api/papers/${paper.slug}/questions`, pick || {},
                     { signal: request.signal });
  } catch (err) {
    clearInterval(tick);
    if (request.signal.aborted || !current()) return;
    showFailure(err);
    return;
  }
  clearInterval(tick);
  if (request.signal.aborted || !current()) return;
  questionsReady = true;

  // --- the questions --------------------------------------------------------
  const questions = made.questions || [];
  const names = made.names || Object.fromEntries(CHOICES);
  const answers = { ...(made.answers || {}), ...(kept(paper.slug, made.key) || {}) };
  const list = h('ol', { class: 'qs-list' });
  const count = h('span', { class: 'qs-count', role: 'status' });
  const go = h('button', { class: 'primary start', type: 'button', text: 'Start the reading' });
  go.addEventListener('click', () => {
    const visible = new Set(shown(questions, answers).map(q => q.id));
    const given = Object.fromEntries(Object.entries(answers).filter(([id]) => visible.has(id)));
    begin(go, { key: made.key, answers: given });
  });

  const item = (q) => {
    const group = h('div', { class: 'qs-choices', role: 'radiogroup', 'aria-label': q.question },
      CHOICES.map(([value]) => {
        const input = h('input', { type: 'radio', name: `q-${q.id}`, value, class: 'sr-only' });
        input.checked = answers[q.id] === value;
        input.addEventListener('change', () => {
          answers[q.id] = value;
          keep(paper.slug, made.key, answers);
          paint();
        });
        return h('label', { class: 'qs-opt' }, input, h('span', { text: names[value] || value }));
      }));
    const meta = [KINDS[q.kind] || '', q.where].filter(Boolean).join(' · ');
    return h('li', { class: `qs-item${q.parent ? ' qs-follow' : ''}`, 'data-id': q.id },
      h('div', { class: 'qs-q' }, mathText(q.question)),
      meta ? h('div', { class: 'qs-meta', text: meta }) : null,
      group);
  };
  const items = new Map(questions.map(q => [q.id, item(q)]));
  items.forEach(el => typeset(el));
  // Every question is in the list from the start, in order; a follow-up is
  // only hidden until its question's answer calls for it. An answer changes
  // which are hidden and nothing else. The list used to be rebuilt on every
  // answer, which took the answer just clicked off the page and put it back:
  // the browser then scrolled to the top of the questions, and every
  // follow-up on screen played its entrance again (2026-10-13).
  mount(list, questions.map(q => items.get(q.id)));

  function paint() {
    const visible = shown(questions, answers);
    const on = new Set(visible.map(q => q.id));
    items.forEach((el, id) => { el.hidden = !on.has(id); });
    const done = visible.filter(q => answers[q.id]).length;
    count.textContent = `${done} of ${visible.length} answered`;
    go.textContent = done === visible.length ? 'Start the reading'
      : `Start the reading (${visible.length - done} unanswered)`;
  }

  const by = made.agent?.name;
  const cost = made.cost != null ? ` · $${Number(made.cost).toFixed(2)}` : '';
  const about = made.cached ? `Asked before, by ${by || 'the AI'} — kept, so free this time.`
    : `Chosen by ${by || 'the AI'} in ${duration(made.seconds || 0)}${cost}.`;
  mount(box, head,
    h('p', { class: 'qs-about', text: `${questions.length} questions about `
             + `${many ? 'these papers' : 'this paper'}, with `
             + `follow-ups where an answer calls for them. ${about}` }),
    list,
    h('div', { class: 'launch qs-launch' }, go, count),
    h('p', { class: 'launch-note', text: 'Unanswered questions are treated as not known, but '
             + 'explained more briefly. Your answers are kept with '
             + `${many ? 'this set of papers' : 'this paper'} for next time.` }),
    h('div', { class: 'chosen-foot' }, backButton, skip));
  paint();
  showBeginning(head[0]);
}
