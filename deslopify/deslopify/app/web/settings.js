/* Advanced settings: everything a reader might once have typed on the command
 * line — which AI reads, how hard it thinks, what a reading may cost and how
 * long it may take, which AI explains, whether key terms are linked.
 *
 * Kept in this browser, and applied when a reading starts; the server checks
 * the caps it is sent and refuses what is out of bounds. Left empty, a setting
 * means the server's own value — the command line's, or the harness's. */

import { api, h, mount } from './dom.js';
import * as hints from './hints.js';
import * as lifecycle from './lifecycle.js';
import { deepseekKey, openrouterKey } from './ready.js';

const KEY = 'deslopify.settings';
const DEFAULTS = { reading: null, maxUsd: null, maxMinutes: null,
                   maxConcurrent: null, explainer: null,
                   linkTerms: true, proofMap: false,
                   // A different AI for a later step of a reading: { check,
                   // revision }, each { account, model, effort? } or null for
                   // the reading's own AI.
                   steps: null,
                   // Whether a reading is written for the background the reader
                   // gave (cvSection): unticked in the launch panel, it is not.
                   useCv: true,
                   // 'light' or 'dark', or null for as this computer is set
                   // (theme.js reads it before the page is drawn).
                   theme: null,
                   level: null };          // the level last chosen before Start

let current = load();
const listeners = new Set();
// What was chosen on this page since it opened. A model named on the command
// line is where the page starts each time — but a choice made here since then
// is the reader's latest word, and wins.
const chosenNow = new Set();

export function chosen(key) { return chosenNow.has(key); }
let menuCache = null;

function stored(key) {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; }
}

function load() {
  const saved = stored(KEY);
  if (saved) return { ...DEFAULTS, ...saved };
  // The two choices the page remembered before there were settings, so that
  // nothing a reader chose is lost by their arrival.
  return { ...DEFAULTS, reading: stored('deslopify.agent'),
           explainer: stored('deslopify.explainer') };
}

export function get() { return current; }

// `source` lets a control that made a change ignore its own echo.
export function set(patch, source = null) {
  Object.keys(patch).forEach(k => chosenNow.add(k));
  current = { ...current, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(current)); }
  catch { /* a private window, or storage off: settings last as long as the page */ }
  listeners.forEach(fn => fn(current, patch, source));
}

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Light or dark, applied the moment it is chosen — or reset to defaults.
onChange((now, patch) => {
  if (!('theme' in patch)) return;
  const root = document.documentElement;
  if (now.theme === 'light' || now.theme === 'dark') root.dataset.theme = now.theme;
  else delete root.dataset.theme;
});

// The AI menu, fetched once per page: the accounts and models that can run —
// and again after Getting ready has looked, since it says who is signed in.
window.addEventListener('unpacman:ready', () => { menuCache = null; menus.clear(); });

export async function menu() {
  if (!menuCache) menuCache = api('GET', '/api/agents').catch(() => { menuCache = null; return null; });
  return menuCache;
}

// The same menu for a search or a joint reading, whose default limits are
// their own (the AI and the models are the same).
const menus = new Map();
export async function menuFor(level) {
  if (!level || level === 'expert' || level === 'general') return menu();
  if (!menus.has(level)) {
    menus.set(level, api('GET', `/api/agents?level=${encodeURIComponent(level)}`)
      .catch(() => { menus.delete(level); return null; }));
  }
  return menus.get(level);
}

// A choice of AI as the menu still offers it, or null: a model the programs no
// longer list (catalog.py), or an AI gone from this computer, is not asked for.
export function offeredPick(pick, menuData) {
  if (!pick) return null;
  const g = (menuData?.groups || []).find(x => x.account === pick.account);
  return g && (pick.custom || g.models.some(m => m.id === pick.model)) ? pick : null;
}

// The caps a reading on this model will have, as the server will apply them.
export function capsFor(model, menuData) {
  const s = current;
  const usd = model?.priced ? (s.maxUsd ?? model.cap?.usd ?? menuData?.limits?.max_usd) : null;
  const minutes = s.maxMinutes ?? model?.cap?.minutes ?? menuData?.limits?.max_minutes;
  return { usd: usd ?? null, minutes: minutes ?? null };
}

// What a reading request carries from the settings.
export function readingCaps() {
  const out = {};
  if (current.maxUsd != null) out.max_usd = current.maxUsd;
  if (current.maxMinutes != null) out.max_minutes = current.maxMinutes;
  if (current.maxConcurrent != null) out.max_concurrent = current.maxConcurrent;
  return out;
}

// The later steps of a reading given to another AI, as the menu offers them:
// each { slot, title, g, m, effort } — a step whose AI is not offered here
// (uninstalled since, say) is left out, and runs on the reading's own.
export function stepChoices(menuData) {
  const wanted = current.steps || {};
  const groups = menuData?.groups || [];
  const slots = (menuData?.steps || []).filter(st => st.slot !== 'draft');
  const out = [];
  for (const st of slots) {
    const want = wanted[st.slot];
    if (!want) continue;
    const g = groups.find(x => x.account === want.account);
    const m = g?.models.find(x => x.id === want.model)
      || (g && want.custom ? customModel(want.model, menuData) : null);
    if (g && m) out.push({ slot: st.slot, title: st.title, g, m, effort: want.effort || null });
  }
  return out;
}

// What a reading request carries for them.
export function readingSteps(menuData) {
  const picked = stepChoices(menuData);
  if (!picked.length) return {};
  return { steps: Object.fromEntries(picked.map(c => [c.slot, {
    account: c.g.account, model: c.m.id, ...(c.m.custom ? { custom: true } : {}),
    ...(c.effort ? { effort: c.effort } : {}) }])) };
}

// The writer creates a requested diagram only for a pure-mathematics paper.
export function readingOptions() {
  return current.proofMap ? { include_map: true } : {};
}

// ------------------------------------------------------------------- dialog

const EFFORT_HELP = 'Higher effort reads more carefully and costs more.';
// Suggested budgets beside the time fields; the launch panel highlights
// values below the lower end of the range for that kind of run.
export const RECOMMENDED_MINUTES = 20;
export const TIME_ADVICE = 'Good things take time. Suggested budget: at least 20mn for a companion.';
export const SEARCH_RECOMMENDED_MINUTES = 5;
export const SEARCH_TIME_ADVICE = 'Good things take time. Suggested budget: at least 5mn for fetching papers.';
const SMALL_MODEL = /haiku|(?:^|-)(?:mini|nano)(?:-|$)/i;

function customModel(id, data) {
  return {
    id, name: id, custom: true, small: SMALL_MODEL.test(id), priced: false,
    cap: { usd: null, minutes: data?.limits?.max_minutes ?? null },
  };
}

function accountSelect(groups, value, label) {
  const sel = h('select', { 'aria-label': label },
    groups.map(g => h('option', { value: g.account, text: g.label })));
  sel.value = groups.some(g => g.account === value) ? value : (groups[0]?.account || '');
  return sel;
}

function guessedProvider(model) {
  if (/^claude(?:-|$)/i.test(model)) return 'claude';
  if (/^(?:gpt|codex)(?:-|$)/i.test(model) || /^o\d/i.test(model)) return 'codex';
  return null;
}

function modelSelect(groups, value, { label, first = null }) {
  const sel = h('select', { 'aria-label': label },
    first ? h('option', { value: '', text: first }) : null,
    groups.map((g, gi) => h('optgroup', { label: g.label },
      g.models.map((m, mi) => h('option', { value: `${gi}:${mi}`, text: m.name })))));
  const gi = groups.findIndex(g => g.account === value?.account);
  const mi = gi >= 0 ? groups[gi].models.findIndex(m => m.id === value?.model) : -1;
  sel.value = gi >= 0 && mi >= 0 ? `${gi}:${mi}` : (first ? '' : '0:0');
  return sel;
}

function picked(groups, sel) {
  if (!sel.value) return null;
  const [gi, mi] = sel.value.split(':').map(Number);
  return { g: groups[gi], m: groups[gi].models[mi] };
}

function numberField({ label, unit, prefix, value, placeholder, range, onSet, help,
                       integer = false }) {
  const input = h('input', {
    type: 'number', inputmode: 'decimal', min: String(range[0]), max: String(range[1]),
    step: integer ? '1' : 'any', placeholder: placeholder != null ? String(placeholder) : '',
    value: value != null ? String(value) : '', 'aria-label': label,
  });
  const err = h('div', { class: 'set-error', hidden: true });
  input.addEventListener('change', () => {
    const raw = input.value.trim();
    if (!raw) { err.hidden = true; onSet(null); return; }
    const n = Number(raw);
    if (!Number.isFinite(n) || (integer && !Number.isInteger(n))
        || n < range[0] || n > range[1]) {
      const interval = `${prefix || ''}${range[0]} and ${prefix || ''}${range[1]}`
        + (unit ? ` ${unit}` : '');
      err.textContent = integer ? `A whole number between ${interval}.` : `Between ${interval}.`;
      err.hidden = false;
      return;
    }
    err.hidden = true;
    onSet(n);
  });
  return h('label', { class: 'set-field' },
    h('span', { class: 'set-label', text: label }),
    h('span', { class: 'set-input' }, prefix ? h('span', { text: prefix }) : null, input,
      unit ? h('span', { text: unit }) : null),
    help ? h('span', { class: 'set-help', text: help }) : null, err);
}

// `at`: a part of the dialog to bring into view as it opens ("method").
export async function open({ at = null } = {}) {
  const data = await menu();
  const groups = data?.groups || [];
  const dialog = h('dialog', { class: 'settings', 'aria-labelledby': 'settings-title' });
  document.body.append(dialog);

  const s = current;
  const start = s.reading && (!data?.pinned || chosen('reading')) ? s.reading : data?.default;
  const readSel = groups.length ? modelSelect(groups, start, { label: 'AI for creating the companion' }) : null;
  const readCustom = h('input', {
    type: 'text', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'e.g. gpt-6.1-sol', 'aria-label': 'Custom model ID for creating the companion',
    value: start?.custom ? start.model : '',
  });
  const readAccount = groups.length
    ? accountSelect(groups, start?.account, 'Account for the custom companion model') : null;
  const effortSel = h('select', { 'aria-label': 'Effort' });
  const smallNote = h('div', { class: 'set-warn', hidden: true });
  const capNote = h('div', { class: 'set-help' });

  const fillEfforts = () => {
    const custom = readCustom.value.trim();
    const customGroup = groups.find(g => g.account === readAccount?.value);
    const at = custom && customGroup
      ? { g: customGroup, m: customModel(custom, data) }
      : readSel && picked(groups, readSel);
    const efforts = at?.g.efforts || [];
    const want = effortSel.value || s.reading?.effort || data?.default?.effort || 'high';
    mount(effortSel, efforts.map(e => h('option', { value: e.id, text: e.name })));
    effortSel.value = efforts.some(e => e.id === want) ? want
      : efforts.some(e => e.id === 'high') ? 'high' : (efforts[0]?.id || '');
    smallNote.hidden = !at?.m.small;
    smallNote.textContent = at?.m.small
      ? `${at.m.name} is a small model: fine for trying unpacman out, but small models `
        + 'tend to miss the very gaps it exists to find.' : '';
    paintCapNote(at);
  };
  // Which cap a reading has: both — except that the money cap cannot count a
  // step whose AI has no price, and is dropped if none of them has one.
  const paintCapNote = (at = null) => {
    const first = at || (readSel && picked(groups, readSel));
    const steps = stepChoices(data);
    const all = [first ? { m: first.m, title: 'the first reading' } : null,
                 ...steps.map(c => ({ m: c.m, title: c.title.toLowerCase() }))].filter(Boolean);
    const off = all.find(x => !x.m.priced);
    capNote.textContent = !off
      ? 'The reading stops at whichever cap it reaches first. On a subscription, the '
        + 'cost is what the same work would cost through the API.'
      : !all.some(x => x.m.priced)
        ? `${off.m.name} has no per-token price here, so only the time cap applies to it.`
        : `${off.m.name}${steps.length ? ` (${off.title})` : ''} has no per-token price here: `
          + 'the money cap counts the other steps, and the time cap all of them.';
    capNote.textContent += ' Review and revision may each use up to 5 extra minutes (up to 10 extra in total).';
  };
  const saveReading = () => {
    const custom = readCustom.value.trim();
    if (custom) {
      const g = groups.find(group => group.account === readAccount?.value);
      if (g) set({ reading: { account: g.account, model: custom,
                              effort: effortSel.value, custom: true } });
      return;
    }
    const at = picked(groups, readSel);
    if (at) set({ reading: { account: at.g.account, model: at.m.id, effort: effortSel.value } });
  };
  if (readSel) {
    readSel.addEventListener('change', () => {
      readCustom.value = '';
      fillEfforts(); saveReading();
    });
    readCustom.addEventListener('change', () => {
      const provider = guessedProvider(readCustom.value.trim());
      const inferred = groups.find(g => g.provider === provider);
      if (inferred) readAccount.value = inferred.account;
      fillEfforts(); saveReading();
    });
    readAccount.addEventListener('change', () => { fillEfforts(); saveReading(); });
    effortSel.addEventListener('change', saveReading);
    fillEfforts();
  }

  // Each later step of a reading on an AI of its own (stepChoices).
  const stepSlots = (data?.steps || []).filter(st => st.slot !== 'draft');
  const stepRows = stepSlots.map((st) => {
    const want = s.steps?.[st.slot] || null;
    const sel = modelSelect(groups, want, { label: `Which AI does ${st.title.toLowerCase()}`,
                                            first: 'The reading’s own AI' });
    const eff = h('select', { 'aria-label': `Effort for ${st.title.toLowerCase()}` });
    const fill = () => {
      const at = picked(groups, sel);
      const efforts = at?.g.efforts || [];
      const was = eff.value || want?.effort || '';
      mount(eff, h('option', { value: '', text: 'its usual effort' }),
            efforts.map(e => h('option', { value: e.id, text: e.name })));
      eff.value = efforts.some(e => e.id === was) ? was : '';
      eff.disabled = !at;
    };
    const save = () => {
      const at = picked(groups, sel);
      const steps = { ...(current.steps || {}) };
      steps[st.slot] = at ? { account: at.g.account, model: at.m.id,
                              ...(eff.value ? { effort: eff.value } : {}) } : null;
      set({ steps: Object.values(steps).some(Boolean) ? steps : null });
      paintCapNote();
    };
    sel.addEventListener('change', () => { fill(); save(); });
    eff.addEventListener('change', save);
    fill();
    return h('div', { class: 'set-field set-step' },
      h('span', { class: 'set-label', text: st.title }),
      h('span', { class: 'set-input step-input' }, sel, eff),
      h('span', { class: 'set-help', text: st.detail }));
  });

  const explainSel = groups.length
    ? modelSelect(groups, s.explainer, { label: 'AI for explanations', first: 'The AI that created the companion' })
    : null;
  const explainCustom = h('input', {
    type: 'text', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'optional model ID', 'aria-label': 'Custom explanation model ID',
    value: s.explainer?.custom ? s.explainer.model : '',
  });
  const explainAccount = groups.length
    ? accountSelect(groups, s.explainer?.account, 'Account for the custom explanation model') : null;
  const saveExplainer = () => {
    const custom = explainCustom.value.trim();
    if (custom) {
      const g = groups.find(group => group.account === explainAccount?.value);
      set({ explainer: g ? { account: g.account, model: custom, custom: true } : null });
      return;
    }
    const at = picked(groups, explainSel);
    set({ explainer: at ? { account: at.g.account, model: at.m.id } : null });
  };
  explainSel?.addEventListener('change', () => {
    explainCustom.value = '';
    saveExplainer();
  });
  explainCustom.addEventListener('change', () => {
    const provider = guessedProvider(explainCustom.value.trim());
    const inferred = groups.find(g => g.provider === provider);
    if (inferred) explainAccount.value = inferred.account;
    saveExplainer();
  });
  explainAccount?.addEventListener('change', saveExplainer);
  const terms = h('input', { type: 'checkbox', checked: s.linkTerms ? true : null });
  terms.addEventListener('change', () => set({ linkTerms: terms.checked }));

  const limits = data?.limits || {};
  const accountCaps = [...new Set(groups.map(g => g.max_concurrent).filter(Boolean))];
  const accountLimit = accountCaps.length === 1
    ? ` The configured account limit is ${accountCaps[0]}.`
    : accountCaps.length
      ? ` Configured account limits range from ${Math.min(...accountCaps)} to ${Math.max(...accountCaps)}.`
      : '';
  const close = () => { dialog.close(); dialog.remove(); };
  mount(dialog,
    h('form', { method: 'dialog', class: 'settings-form', onsubmit: (e) => { e.preventDefault(); close(); } },
      h('h2', { id: 'settings-title', text: 'Advanced settings' }),
      h('p', { class: 'set-intro',
               text: 'Choices are kept in this browser. Companion settings apply to the next run; '
                     + 'explanation settings apply to new questions and key-term explanations. '
                     + 'Leave a field empty to use the default.' }),
      appearanceSection(),
      lifecycle.section(close),

      cvSection(),

      data?.pinned ? h('p', { class: 'set-help',
        text: 'unpacman was started with a model named on the command line, so the AI for '
              + 'creating the companion starts from that one each time the page opens; what you choose here applies '
              + 'until then.' }) : null,

      h('fieldset', {},
        h('legend', { text: 'For creating the companion' }),
        readSel ? h('label', { class: 'set-field' }, h('span', { class: 'set-label', text: 'AI' }), readSel,
          h('span', { class: 'set-help', text: 'Reads the source paper and writes the companion. '
            + 'Also checks and revises it unless you choose another AI for those steps below.' })) : null,
        readSel ? h('label', { class: 'set-field' },
          h('span', { class: 'set-label', text: 'Custom model' }), readCustom,
          h('span', { class: 'set-help', text: 'Optional. Enter the exact model ID accepted by the provider CLI.' })) : null,
        readAccount ? h('label', { class: 'set-field' },
          h('span', { class: 'set-label', text: 'Run custom with' }), readAccount) : null,
        readSel ? h('label', { class: 'set-field' }, h('span', { class: 'set-label', text: 'Effort' }), effortSel,
          h('span', { class: 'set-help', text: EFFORT_HELP })) : null,
        smallNote,
        groups.length ? null : h('p', { class: 'set-warn',
          text: 'No AI is available to read with here. Install Claude Code or Codex, then start unpacman again.' })),

      stepRows.length ? h('fieldset', { class: 'set-steps' },
        h('legend', { text: 'Companion creation steps' }),
        h('p', { class: 'set-help-block',
                 text: 'Creating a companion takes three steps: the first writes it, the check reads '
                       + 'it against the paper, and the revision answers the check. Each can run '
                       + 'on a different AI — a check by another model is a second opinion, '
                       + 'not the same reader checking its own work.' }),
        h('div', { class: 'set-field set-step' },
          h('span', { class: 'set-label', text: 'First draft' }),
          h('span', { class: 'set-help step-first', text: 'The AI chosen under “For creating the companion”, above.' })),
        stepRows) : null,

      h('fieldset', {},
        h('legend', { text: 'Limits for each reading' }),
        numberField({ label: 'Money cap', prefix: '$', value: s.maxUsd, placeholder: limits.max_usd,
                      range: limits.usd_range || [0.1, 200], onSet: (v) => set({ maxUsd: v }) }),
        numberField({ label: 'Planned time', unit: 'minutes', value: s.maxMinutes,
                      placeholder: limits.max_minutes, range: limits.minutes_range || [1, 600],
                      onSet: (v) => set({ maxMinutes: v }),
                      help: 'Good things take time. Suggested budgets: at least 20mn for a companion; '
                            + 'at least 5mn for fetching papers.' }),
        numberField({ label: 'Parallel readings', value: s.maxConcurrent,
                      placeholder: limits.max_concurrent_runs,
                      range: limits.concurrent_range || [1, limits.max_concurrent_runs || 1],
                      integer: true, onSet: (v) => set({ maxConcurrent: v }),
                      help: 'Applies across all papers. The server and selected AI account may impose a lower hard limit; only one run per paper and mode is allowed.'
                            + accountLimit }),
        capNote),

      h('fieldset', {},
        h('legend', { text: 'For explanations' }),
        explainSel ? h('label', { class: 'set-field' }, h('span', { class: 'set-label', text: 'AI' }), explainSel,
          h('span', { class: 'set-help', text: 'Explains selected passages, answers follow-up questions, '
            + 'and explains key terms. Does not create or rewrite the companion.' })) : null,
        explainSel ? h('label', { class: 'set-field' },
          h('span', { class: 'set-label', text: 'Custom model' }), explainCustom,
          h('span', { class: 'set-help', text: 'Optional; runs through the account selected below.' })) : null,
        explainAccount ? h('label', { class: 'set-field' },
          h('span', { class: 'set-label', text: 'Run custom with' }), explainAccount) : null,
        h('label', { class: 'set-check' }, terms,
          h('span', {}, h('b', { text: 'Link key terms in companions. ' }),
            'The terms a companion most needs become links, Wikipedia style; point at one for a '
            + 'short explanation. One quick request per companion, kept with the paper.'))),

      openSection(dialog),

      methodSection(dialog),

      h('div', { class: 'set-foot' },
        h('span', { class: 'method-tools' },
          h('button', { type: 'button', class: 'linkish', text: 'Reset to defaults',
                        onclick: () => { set({ ...DEFAULTS }); close(); open(); } }),
          h('button', { type: 'button', class: 'linkish', text: 'Show the tips again',
                        title: 'Each screen’s tips, the next time you open it',
                        onclick: () => { hints.reset(); close(); } })),
        h('button', { type: 'submit', class: 'primary', text: 'Done' }))));
  // Escape, or a click outside, closes the dialog — unless a file is being
  // edited, when it asks first (the editor's own `leave`).
  dialog.addEventListener('cancel', (e) => {
    e.preventDefault();
    if (!dialog.leave || dialog.leave()) close();
  });
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog && (!dialog.leave || dialog.leave())) close();
  });
  dialog.showModal();
  if (at === 'method') dialog.querySelector('.method')?.scrollIntoView({ block: 'start' });
  if (at === 'open') dialog.querySelector('.set-open')?.scrollIntoView({ block: 'start' });
  if (at === 'cv') {
    dialog.querySelector('.set-cv')?.scrollIntoView({ block: 'start' });
    dialog.querySelector('.cv-text')?.focus({ preventScroll: true });
  }
  if (at === 'steps') {
    const steps = dialog.querySelector('.set-steps');
    steps?.scrollIntoView({ block: 'start' });
    steps?.querySelector('select')?.focus({ preventScroll: true });
  }
}

// ------------------------------------------------------------- appearance

// Light or dark, or as this computer is set (Tancredi, 2026-10-23).
function appearanceSection() {
  const now = current.theme === 'light' || current.theme === 'dark' ? current.theme : '';
  const choice = (value, label) => h('label', { class: 'set-radio' },
    h('input', { type: 'radio', name: 'set-theme', value, checked: value === now,
                 onchange: () => set({ theme: value || null }) }),
    h('span', { text: label }));
  return h('fieldset', { class: 'set-theme' },
    h('legend', { text: 'Appearance' }),
    h('div', { class: 'set-field' },
      h('span', { class: 'set-label', text: 'Colours' }),
      h('span', { class: 'set-radios' },
        choice('', 'Automatic'), choice('light', 'Light'), choice('dark', 'Dark')),
      h('span', { class: 'set-help', text: 'Automatic follows the light or dark appearance '
                                           + 'set for this computer.' })));
}

// ------------------------------------------- your mathematical background

// What the reader knows, in their own words or from a CV file (cv.py): every
// reading they start is written for it (unless they untick it as they start
// one), and the questions before a Non-expert reading and the explanations
// are pitched to it. Kept as it is typed, a moment after the typing stops.
export async function background() {
  try { return (await api('GET', '/api/cv')).text || ''; } catch { return ''; }
}

function cvSection() {
  const text = h('textarea', {
    class: 'cv-text', rows: '7', spellcheck: 'true', 'aria-label': 'Your mathematical background',
    placeholder: 'e.g. Differential topologist (PhD 2015): smooth manifolds, characteristic '
                 + 'classes, surgery. At home in homotopy theory; little analysis or PDE; no physics.',
  });
  const state = h('span', { class: 'cv-state', role: 'status' });
  const msg = h('div', { class: 'set-error', hidden: true });
  const count = h('span', { class: 'cv-count' });
  let saved = '';
  let max = 12000;
  let timer = 0;
  const paintCount = () => {
    const n = text.value.trim() ? text.value.trim().split(/\s+/).length : 0;
    count.textContent = n ? `${n} word${n === 1 ? '' : 's'}` : '';
    count.classList.toggle('over', text.value.length > max);
  };
  const keep = async () => {
    clearTimeout(timer);
    if (text.value.trim() === saved.trim()) { state.textContent = saved ? 'Saved' : ''; return; }
    state.textContent = 'Saving…';
    try {
      const r = await api('PUT', '/api/cv', { text: text.value });
      saved = r.text || '';
      msg.hidden = true;
      state.textContent = saved ? 'Saved' : 'Deleted';
      forget.hidden = !saved;
      window.dispatchEvent(new CustomEvent('unpacman:cv', { detail: saved }));
    } catch (err) {
      msg.className = 'set-error';
      msg.textContent = err.message;
      msg.hidden = false;
      state.textContent = 'Not saved';
    }
  };
  text.addEventListener('input', () => {
    paintCount();
    state.textContent = '';
    clearTimeout(timer);
    timer = setTimeout(keep, 900);
  });
  text.addEventListener('blur', keep);
  // Closing the dialog right after typing: kept all the same.
  text.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); keep(); }
  });

  // A CV already written: its text, read here (a PDF too), to look over and
  // shorten before it is kept. As the drop boxes, the input's own label.
  const file = h('input', { type: 'file', accept: '.pdf,.txt,.md,.tex,application/pdf,text/plain',
                            hidden: true });
  const fromFile = h('label', { class: 'ghost cv-file', tabindex: '0' }, file, 'Read it from a file…');
  fromFile.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); }
  });
  file.addEventListener('change', async () => {
    const chosen = file.files[0];
    file.value = '';
    if (!chosen) return;
    const form = new FormData();
    form.set('file', chosen);
    state.textContent = 'Reading the file…';
    try {
      const r = await api('POST', '/api/cv/file', form);
      text.value = r.text;
      paintCount();
      msg.hidden = !r.cut;
      msg.className = 'set-warn';
      msg.textContent = r.cut ? `Only the first ${Number(r.max).toLocaleString()} characters of `
        + `${r.name} fit; the rest was left out. Keep what says what you know.` : '';
      await keep();
    } catch (err) {
      msg.className = 'set-error';
      msg.textContent = err.message;
      msg.hidden = false;
      state.textContent = '';
    }
  });
  const forget = h('button', { class: 'linkish', type: 'button', text: 'Delete it', hidden: true,
    onclick: async () => {
      if (!confirm('Delete your mathematical background from this computer?')) return;
      clearTimeout(timer);
      try { await api('DELETE', '/api/cv'); } catch (err) { msg.textContent = err.message; msg.hidden = false; return; }
      saved = '';
      text.value = '';
      paintCount();
      forget.hidden = true;
      state.textContent = 'Deleted';
      window.dispatchEvent(new CustomEvent('unpacman:cv', { detail: '' }));
    } });
  api('GET', '/api/cv').then((r) => {
    saved = r.text || '';
    max = r.max || max;
    if (!text.value) text.value = saved;
    text.maxLength = max;
    forget.hidden = !saved;
    state.textContent = saved ? 'Saved' : '';
    paintCount();
  }).catch(() => { state.textContent = 'Could not be loaded.'; });

  return h('fieldset', { class: 'set-cv' },
    h('legend', { text: 'Your mathematical background' }),
    h('p', { class: 'set-help-block', text: 'Say what you know — a few lines, or your CV: your '
             + 'field, what you work on, what you know well and what you do not. Every reading '
             + 'you start is then written for you, and the questions before a Non-expert reading '
             + 'and the explanations are pitched to it.' }),
    text,
    h('div', { class: 'cv-row' }, fromFile, forget, h('span', { class: 'cv-gap' }), count, state),
    msg,
    h('p', { class: 'set-help-block cv-note', text: 'Kept on this computer, and sent to the AI '
             + 'with each reading, question and explanation, as the paper is. Leave out what you '
             + 'would not send: your address, your telephone number.' }));
}

// ------------------------------------------------------ other ways to read

// Reading without a Claude or ChatGPT plan (openweight.py): DeepSeek's own
// service and OpenRouter, each with a key of one's own, and open-weight models
// on this Mac (Ollama). What each needs, said plainly, and what the reader
// may do here: paste a key, or download a model.
function openSection(dialog) {
  const body = h('div', { class: 'open-body' }, h('p', { class: 'set-help-block' },
    h('span', { class: 'spin' }), ' Looking…'));
  const section = h('fieldset', { class: 'set-open' },
    h('legend', { text: 'Other ways to read' }),
    h('p', { class: 'set-help-block',
             text: 'Three ways to read without a Claude or ChatGPT plan: DeepSeek’s own service '
                   + 'and OpenRouter, both paid per use, and open-weight models on this Mac, at no '
                   + 'cost. Each reads through Claude Code, which must be installed but needs no '
                   + 'Claude plan for them. Only DeepSeek’s own service is known to search the web '
                   + 'here: with the others, the references in a companion may come from memory, '
                   + 'so check them.' }),
    body);
  const paint = async (fresh = false) => {
    let st;
    try { st = await api('GET', `/api/open${fresh ? '?fresh=1' : ''}`); }
    catch (err) { mount(body, h('p', { class: 'set-error', text: err.message })); return; }
    const o = st.ollama;
    // A model downloaded from here, with its progress (openweight.Pull).
    const pullBox = h('div', { class: 'open-pull', role: 'status' });
    const gb = (n) => `${(n / 1e9).toFixed(1)} GB`;
    let polling = false;
    const follow = async () => {
      if (polling) return;
      polling = true;
      while (dialog.isConnected) {
        let pull;
        try { pull = await api('GET', '/api/open/pull'); } catch { break; }
        if (pull.state === 'running') {
          mount(pullBox, h('span', { class: 'spin' }), ` Downloading ${pull.model}`,
            pull.total ? `: ${gb(pull.completed)} of ${gb(pull.total)}` : '…', ' ',
            h('button', { class: 'linkish', type: 'button', text: 'Stop',
                          onclick: () => api('DELETE', '/api/open/pull').catch(() => null) }));
          await new Promise(r => setTimeout(r, 1000));
          continue;
        }
        if (pull.state === 'done') {
          window.dispatchEvent(new Event('unpacman:ready'));
          polling = false;
          paint(true);
          return;
        }
        mount(pullBox, pull.state === 'failed' ? h('span', { class: 'set-error', text: pull.error })
          : pull.state === 'cancelled' ? 'The download was stopped.' : null);
        break;
      }
      polling = false;
    };
    const download = (id) => h('button', { class: 'linkish', type: 'button', text: 'Download',
      onclick: async () => {
        try { await api('POST', '/api/open/pull', { model: id }); }
        catch (err) { mount(pullBox, h('span', { class: 'set-error', text: err.message })); return; }
        follow();
      } });
    const have = new Set((o.models || []).map(m => m.id));
    const fits = (o.suggested || []).filter(m => !have.has(m.id)).map(m => h('li', {},
      h('code', { text: m.id }), ` — ${m.about}`, o.running ? [' · ', download(m.id)] : null));
    api('GET', '/api/open/pull').then(pull => { if (pull.state === 'running') follow(); }).catch(() => null);
    const local = !o.installed
      ? [h('p', { class: 'set-help-block' },
          'Ollama runs open-weight models on this Mac: private, and at no cost — but while it '
          + 'reads, a model takes most of the Mac’s memory, and other programs slow down. ',
          h('a', { href: 'https://ollama.com/download', target: '_blank', rel: 'noopener',
                   text: 'Download Ollama' }),
          ', open it, then download a model that can use tools. ',
          fits.length ? `With ${o.memory_gb} GB of memory, this Mac has room for:` : ''),
         fits.length ? h('ul', { class: 'open-fits' }, fits) : null]
      : !o.running
        ? [h('p', { class: 'set-help-block', text: 'Ollama is installed, but not running. Open it, then check again.' })]
        : [h('p', { class: 'set-help-block', text: o.models.length
              ? `Ollama ${o.version} is running, with ${o.models.length} model${o.models.length === 1 ? '' : 's'} that can read. `
                + 'While one reads, it takes most of this Mac’s memory: close what you can, or use OpenRouter.'
              : `Ollama ${o.version} is running, but has no model that can read yet — one that can use tools.` }),
           o.models.length ? h('ul', { class: 'open-models' }, o.models.map(m => h('li', {},
             h('code', { text: m.id }), m.note ? ` — ${m.note}` : '', m.small ? ' · small' : ''))) : null,
           o.other.length ? h('p', { class: 'set-help-block',
             text: `Not offered, since they cannot use tools: ${o.other.map(m => m.id).join(', ')}.` }) : null,
           fits.length ? h('p', { class: 'set-help-block', text: o.models.length
             ? 'Others this Mac has room for:'
             : `With ${o.memory_gb} GB of memory, this Mac has room for:` }) : null,
           fits.length ? h('ul', { class: 'open-fits' }, fits) : null,
           pullBox];
    // OpenRouter's models, once there is a key: Claude's first (openweight.py).
    const r = st.openrouter;
    const offered = r.key && r.models.length
      ? h('p', { class: 'set-help-block' },
          `Offered, at OpenRouter’s prices: ${r.models.slice(0, 8).map(m => m.name).join(', ')}`
          + (r.models.length > 8 ? `, and ${r.models.length - 8} more` : '') + '.')
      : null;
    mount(body,
      !st.engine ? h('p', { class: 'set-warn-block',
        text: 'Claude Code, which these read through, is not installed. Getting ready installs it.' }) : null,
      h('div', { class: 'open-source' }, h('h3', { text: 'DeepSeek' }),
        h('p', { class: 'set-help-block', text: 'DeepSeek’s own service, paid in advance per use. '
                 + 'Its models read with web search, as Claude and ChatGPT do.' }),
        deepseekKey(() => reopenAt(dialog, 'open'))),
      h('div', { class: 'open-source' }, h('h3', { text: 'OpenRouter' }),
        h('p', { class: 'set-help-block', text: 'One account, paid in advance per use, for models of '
                 + 'many makers. Claude’s own come first: OpenRouter says Claude Code, which unpacman '
                 + 'reads with, is sure to work only with them — and advises making Anthropic the '
                 + 'first provider for them in its settings. The others (DeepSeek, Qwen, Kimi and more) '
                 + 'may work less well. Any other model OpenRouter runs can be named as a custom '
                 + 'model, above. A paper read this way is sent to OpenRouter and to the company '
                 + 'whose model reads it.' }),
        openrouterKey(() => reopenAt(dialog, 'open')), offered),
      h('div', { class: 'open-source' }, h('h3', { text: 'On this Mac' }), local,
        h('button', { class: 'linkish', type: 'button', text: 'Check again', onclick: () => paint(true) })));
  };
  paint();
  return section;
}

function reopenAt(dialog, at) {
  dialog.close();
  dialog.remove();
  open({ at });
}

// ------------------------------------------------------------- the method

// A paper's reading, at either level, first decides whether the paper is pure
// or applied mathematics, then follows the instructions for that kind of
// paper (Antoine's split of 2026-10-12; method.py _PLACES).
const PARTS = [
  ['router', 'Pure or applied?', 'Reads the paper and decides which of the two below to follow.'],
  ['pure', 'Pure mathematics', 'The whole companion method for a paper of pure mathematics.'],
  ['applied', 'Applied mathematics', 'The whole companion method for a paper of applied mathematics.'],
];
const LEVELS = [['expert', 'Expert'], ['nonexpert', 'Non-expert']];

// The readings' files by what they are for: a paper's two levels side by
// side, each with its pure and applied instructions; what a Non-expert
// reading adds; then several papers, a search, and any other file.
function readingsGroups(items, row, edit) {
  const used = new Set();
  const at = (section, level, part) => {
    const f = items.find(x => x.section === section && x.level === level && x.part === part);
    if (f) used.add(f.path);
    return f;
  };
  const badge = (f) => (f.edited ? h('span', { class: 'method-badge', text: 'your version' }) : null);
  const cell = (f, label, about) => h('button', {
    class: 'method-cell', type: 'button', title: f.about || about,
    'aria-label': `${f.title} (${f.name})`, onclick: () => edit(f.path),
  }, h('span', { class: 'method-title' }, label, badge(f)),
     h('span', { class: 'method-name', text: f.name }));
  const columns = LEVELS.map(([level, heading]) => {
    const cells = PARTS.map(([part, label, about]) => {
      const f = at('paper', level, part);
      return f ? cell(f, label, about) : null;
    }).filter(Boolean);
    return cells.length ? h('div', { class: 'method-level' },
      h('div', { class: 'method-level-title', text: heading }), cells) : null;
  }).filter(Boolean);
  const extras = ['questions', 'answers'].map(part => at('paper', 'nonexpert', part)).filter(Boolean);
  const order = { shared: 0, expert: 1, nonexpert: 2 };
  const rank = (f) => order[f.part || f.level] ?? 3;
  const of = (section) => items.filter(f => f.section === section).sort((a, b) => rank(a) - rank(b))
    .map((f) => { used.add(f.path); return f; });
  const together = of('collection');
  const search = of('search');
  const everyone = of('reader');
  const rest = items.filter(f => !used.has(f.path));
  const group = (title, files) => (files.length ? h('div', { class: 'method-group' },
    h('div', { class: 'method-group-title', text: title }), files.map(row)) : null);
  return [
    columns.length ? h('div', { class: 'method-group' },
      h('div', { class: 'method-group-title', text: 'Reading one paper' }),
      h('p', { class: 'set-help-block method-split', text: 'A reading first decides whether the '
               + 'paper is pure or applied mathematics, then follows the instructions for that '
               + 'kind of paper at its level.' }),
      h('div', { class: 'method-levels' }, columns),
      extras.length ? h('div', { class: 'method-also', text: 'A Non-expert reading, of one paper '
                                 + 'or of several together, also uses' }) : null,
      extras.map(row)) : null,
    group('For every reading', everyone),
    group('Several papers, read together', together),
    group('A literature search', search),
    group('Other reading files', rest),
  ];
}

// The files the AI follows, by group, each opening in the editor below. The
// group of files readings started here do not run is folded away.
function methodSection(dialog) {
  const list = h('div', { class: 'method-list' }, h('p', { class: 'set-help-block' },
    h('span', { class: 'spin' }), ' Loading the method…'));
  const section = h('fieldset', { class: 'method' },
    h('legend', { text: 'The method' }),
    h('p', { class: 'set-help-block',
             text: 'The instructions the AI follows, file by file. Your own versions are kept '
                   + 'on this computer and used for the readings and explanations you start '
                   + 'here; the originals are never changed, and any file can be reset.' }),
    list);
  api('GET', '/api/method').then((data) => {
    const byGroup = new Map();
    for (const f of data.files) (byGroup.get(f.group) || byGroup.set(f.group, []).get(f.group)).push(f);
    const row = (f) => h('button', {
      class: 'method-row', type: 'button', onclick: () => editFile(dialog, f.path),
    }, h('span', { class: 'method-title' }, f.title,
         f.edited ? h('span', { class: 'method-badge', text: 'your version' }) : null),
       h('span', { class: 'method-about', text: f.about }),
       h('span', { class: 'method-name', text: f.name }));
    const edited = data.files.filter(f => f.edited);
    mount(list,
      [...byGroup].map(([group, items]) => (group === 'Readings'
        ? readingsGroups(items, row, (path) => editFile(dialog, path))
        : group.startsWith('Not used')
        ? h('details', { class: 'method-group' },
            h('summary', { text: `${group} (${items.length})` }), items.map(row))
        : h('div', { class: 'method-group' },
            h('div', { class: 'method-group-title', text: group }), items.map(row)))),
      edited.length ? h('div', { class: 'method-foot' },
        h('span', { text: `${edited.length} file${edited.length === 1 ? '' : 's'} of yours, kept in ${data.where}.` }),
        h('button', { class: 'linkish', type: 'button', text: 'Reset every file to the original',
                      onclick: async () => {
                        if (!confirm('Put every file back as it ships? Your versions will be deleted.')) return;
                        await api('POST', '/api/method/reset');
                        reopen(dialog);
                      } })) : null);
  }).catch((err) => {
    mount(list, h('p', { class: 'set-error', text: `The method could not be loaded: ${err.message}` }));
  });
  return section;
}

function reopen(dialog) {
  dialog.close();
  dialog.remove();
  open({ at: 'method' });
}

// One file, to read and change. Saved only if the method still loads with it;
// what the server says about it — refused, or a placeholder no longer used —
// is shown in place.
async function editFile(dialog, path) {
  let file;
  try { file = await api('GET', `/api/method/file?path=${encodeURIComponent(path)}`); }
  catch (err) { alert(err.message); return; }

  let saved = file.text;           // what is kept now
  let edited = file.edited;
  let showing = 'mine';            // or 'original'
  const text = h('textarea', { class: 'method-text', spellcheck: 'false', autocomplete: 'off',
                               'aria-label': `${file.title} (${file.name})` });
  text.value = file.text;
  const state = h('span', { class: 'method-state' });
  const msg = h('div', { class: 'method-msg', role: 'status' });
  const saveBtn = h('button', { class: 'primary', type: 'button', text: 'Save' });
  const resetBtn = h('button', { class: 'linkish', type: 'button', text: 'Reset to the original' });
  const swapBtn = h('button', { class: 'linkish', type: 'button' });
  const dirty = () => showing === 'mine' && text.value !== saved;

  const paint = () => {
    state.textContent = showing === 'original' ? 'the original, read only'
      : edited ? 'your version' : 'as it ships';
    resetBtn.hidden = !edited;
    swapBtn.hidden = !edited && !dirty();
    swapBtn.textContent = showing === 'original' ? 'Back to your version' : 'Show the original';
    saveBtn.disabled = showing === 'original' || !dirty();
    text.readOnly = showing === 'original';
    text.classList.toggle('readonly', showing === 'original');
  };
  const say = (kind, ...lines) => mount(msg, lines.filter(Boolean).map(line =>
    h('p', { class: kind === 'error' ? 'set-error' : kind === 'warn' ? 'set-warn-inline' : 'set-ok', text: line })));

  let mine = file.text;            // the reader's text while the original is shown
  swapBtn.addEventListener('click', () => {
    if (showing === 'mine') { mine = text.value; text.value = file.original; showing = 'original'; }
    else { text.value = mine; showing = 'mine'; }
    paint();
  });
  text.addEventListener('input', () => { mount(msg); paint(); });

  const save = async () => {
    if (saveBtn.disabled) return;
    saveBtn.disabled = true;
    try {
      const r = await api('PUT', '/api/method/file', { path, text: text.value });
      saved = text.value;
      edited = r.edited;
      say(r.warnings?.length ? 'warn' : 'ok',
          r.edited ? 'Saved. Readings and explanations you start from now on use your version.'
                   : 'This is the original again, so nothing of yours is kept for this file.',
          ...(r.warnings || []));
    } catch (err) {
      say('error', `Not saved: ${err.message}`);
    }
    paint();
  };
  saveBtn.addEventListener('click', save);
  text.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
  });
  resetBtn.addEventListener('click', async () => {
    if (!confirm(`Put “${file.title}” back as it ships? Your version will be deleted.`)) return;
    try { await api('DELETE', `/api/method/file?path=${encodeURIComponent(path)}`); }
    catch (err) { say('error', err.message); return; }
    saved = file.original;
    mine = file.original;
    text.value = file.original;
    edited = false;
    showing = 'mine';
    say('ok', 'Reset: this file is the original again.');
    paint();
  });

  dialog.leave = () => !dirty() || confirm('Leave without saving your changes?');
  const back = () => { if (dialog.leave()) { dialog.leave = null; reopen(dialog); } };
  dialog.classList.add('wide');
  mount(dialog, h('div', { class: 'settings-form method-editor' },
    h('button', { class: 'linkish method-back', type: 'button', text: '← Advanced settings', onclick: back }),
    h('h2', { id: 'settings-title', text: file.title }),
    h('div', { class: 'method-file' }, h('code', { text: file.name }), ' · ', state),
    file.about ? h('p', { class: 'set-intro', text: file.about }) : null,
    text, msg,
    h('div', { class: 'set-foot' },
      h('span', { class: 'method-tools' }, resetBtn, swapBtn),
      h('span', { class: 'method-tools' },
        h('button', { class: 'linkish', type: 'button', text: 'Done', onclick: back }), saveBtn))));
  paint();
  text.focus();
  text.setSelectionRange(0, 0);
  text.scrollTop = 0;
}
