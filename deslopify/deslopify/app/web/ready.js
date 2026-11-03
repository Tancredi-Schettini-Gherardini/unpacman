/* Getting ready: what this computer still needs before a reading can run —
 * an AI that is installed and signed in, and LaTeX to typeset the companion —
 * with one button for each thing missing (ready.py does the work).
 *
 * The first screen until everything is in place, and reachable later from
 * Advanced settings and from the bar. Nothing here costs money. */

import { api, h, mount, toast } from './dom.js';
import * as feedback from './feedback.js';

let cached = null;            // the last answer from /api/ready
let pending = null;

// What this computer has: the last answer, unless `fresh` asks again.
export async function state({ fresh = false } = {}) {
  if (cached && !fresh) return cached;
  if (pending && !fresh) return pending;
  pending = api('GET', `/api/ready${fresh ? '?fresh=1' : ''}`)
    .then(r => {
      cached = r;
      // The AI menu says who is signed in: it is fetched again after this.
      window.dispatchEvent(new CustomEvent('unpacman:ready', { detail: r }));
      return r;
    })
    .catch(() => cached)
    .finally(() => { pending = null; });
  return pending;
}

export function known() { return cached; }

const PLANS = {
  claude: 'Needs a paid Claude plan: Pro, Max, Team or Enterprise.',
  codex: 'Needs a paid ChatGPT plan: Plus, Pro, Business, Edu or Enterprise.',
};
const PROGRAMS = { claude: 'Claude Code', codex: 'Codex' };

function duration(s) {
  s = Math.round(Number(s) || 0);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')}s`;
}

// The bar's reminder while something is missing.
export function pill() {
  if (!cached || cached.ok) return null;
  return h('a', { class: 'ready-pill', href: '#/ready', text: 'Getting ready',
                  title: 'What this computer still needs before a first reading' });
}

// ------------------------------------------------------------------ page

const jobsShown = new Map();   // kind → job, while a job is on screen
let poller = null;

// `onDone`: what "Continue" does — the home page, or wherever the reader was.
export async function renderReady(root, { onDone }) {
  stopPolling();
  const now = await state({ fresh: true });
  if (!root.isConnected) return;
  if (!now) {
    mount(root, h('section', { class: 'stage ready' },
      h('h1', { text: 'Getting ready' }),
      h('p', { class: 'set-error', text: 'unpacman could not check this computer. Reload the page to try again.' })));
    return;
  }
  for (const job of now.jobs || []) {
    if (job.state === 'running') jobsShown.set(job.kind, job);
  }
  paint(root, now, onDone);
  if ([...jobsShown.values()].some(j => j.state === 'running')) startPolling(root, onDone);
}

function stopPolling() {
  if (poller) { clearInterval(poller); poller = null; }
}

function startPolling(root, onDone) {
  if (poller) return;
  poller = setInterval(async () => {
    if (!root.isConnected) { stopPolling(); return; }
    const running = [...jobsShown.values()].filter(j => j.state === 'running');
    if (!running.length) { stopPolling(); return; }
    // Only the jobs that ended since the last look are told: one that ended
    // before — another account, then back to the Mac's — is history.
    const ended = [];
    for (const job of running) {
      try {
        const next = await api('GET', `/api/ready/job/${job.id}`);
        jobsShown.set(next.kind, next);
        if (next.state !== 'running') ended.push(next);
      } catch { /* the next tick tries again */ }
    }
    const now = ended.length ? await state({ fresh: true }) : cached;
    if (root.isConnected && now) paint(root, now, onDone);
    ended.filter(j => j.state === 'done').forEach(j => toast(j.step || `${j.title}: done.`));
  }, 1000);
}

async function begin(kind, root, onDone) {
  try {
    const job = await api('POST', `/api/ready/${kind}`);
    jobsShown.set(kind, job);
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  paint(root, cached, onDone);
  startPolling(root, onDone);
}

async function cancel(job, root, onDone) {
  try { jobsShown.set(job.kind, await api('POST', `/api/ready/job/${job.id}/cancel`)); }
  catch (err) { toast(err.message, 'err'); }
  paint(root, await state({ fresh: true }), onDone);
}

// One job, as it goes: what it is doing, how long it has taken, and — for a
// sign-in — the page to open again and the box for a code.
function jobView(job, root, onDone) {
  if (!job) return null;
  if (job.state === 'running') {
    const signin = job.kind.startsWith('signin-') || job.kind.startsWith('switch-');
    const code = h('input', { type: 'text', class: 'ready-code', autocomplete: 'off',
                              spellcheck: 'false', placeholder: 'Paste the code here',
                              'aria-label': 'The code shown by the sign-in page',
                              'data-keep': `code-${job.kind}` });
    const send = h('button', { class: 'ghost', type: 'button', text: 'Send',
      onclick: async () => {
        if (!code.value.trim()) return;
        send.disabled = true;
        try { await api('POST', `/api/ready/job/${job.id}/code`, { code: code.value.trim() }); toast('Sent.'); }
        catch (err) { toast(err.message, 'err'); }
        send.disabled = false;
      } });
    return h('div', { class: 'ready-job', role: 'status' },
      h('div', { class: 'ready-step' }, h('span', { class: 'spin' }), ' ', job.step || job.title,
        h('span', { class: 'ready-time', text: ` · ${duration(job.elapsed)}` })),
      signin && job.url ? h('div', { class: 'ready-sub' },
        'No page opened? ', h('a', { href: job.url, target: '_blank', rel: 'noopener', text: 'Open the sign-in page' }), '.') : null,
      job.waiting_for_code ? h('div', { class: 'ready-sub ready-coderow' },
        h('span', { text: 'If the page shows a code instead of returning here, paste it:' }),
        h('span', { class: 'ready-coderow-in' }, code, send)) : null,
      h('button', { class: 'linkish', type: 'button', text: 'Cancel',
                    onclick: () => cancel(job, root, onDone) }));
  }
  if (job.state === 'failed') {
    return h('div', { class: 'ready-job failed', role: 'alert' },
      h('div', { class: 'set-error', text: job.error || 'That did not work.' }),
      job.output?.length ? h('details', { class: 'ready-details' },
        h('summary', { text: 'What it said' }),
        h('pre', { text: job.output.join('\n') })) : null,
      h('button', { class: 'linkish', type: 'button', text: 'Tell us about it',
                    onclick: () => feedback.open({
                      kind: 'problem', about: `${job.title || job.kind}: ${job.error || 'it did not work'}` }) }));
  }
  if (job.state === 'cancelled') {
    return h('div', { class: 'ready-job', text: 'Stopped.' });
  }
  return null;
}

// `optional`: not ready, and not needed either — the second AI, once the
// first is signed in.
function card({ title, state: on, optional = false, lines, actions, job, root, onDone }) {
  const badge = on ? ['ok', 'Ready'] : optional ? ['optional', 'Optional'] : ['todo', 'Needed'];
  return h('div', { class: `ready-card${on ? ' on' : ''}` },
    h('div', { class: 'ready-card-head' },
      h('b', { text: title }),
      h('span', { class: `ready-state ${badge[0]}`, text: badge[1] })),
    lines.filter(Boolean).map(line => (typeof line === 'string'
      ? h('div', { class: 'ready-line', text: line }) : line)),
    job && job.state === 'running' ? null
      : actions?.filter(Boolean).length ? h('div', { class: 'ready-actions' }, actions) : null,
    jobView(job, root, onDone));
}

// Another account for the same AI, for unpacman alone (2026-10-17): it signs
// in in a folder of unpacman's own (ready.py Checker._switch, signins.py), so
// Claude Code and Codex elsewhere on the Mac — in Terminal, say — keep the
// sign-in they have, and nothing changes until the new sign-in has finished.
// Going back to the Mac's sign-in signs unpacman's own out.
const SITES = { claude: 'claude.ai', codex: 'chatgpt.com' };

function confirmAnother(provider, a) {
  const name = provider === 'claude' ? 'Claude' : 'ChatGPT';
  const offered = a.who ? ` If the page offers ${a.who}, sign out of ${SITES[provider]} in `
                          + 'that browser first.' : '';
  return confirm(`Sign in to ${name} with another account, for unpacman only?\n\n`
                 + 'A sign-in page opens in your browser: sign in there with the other account. '
                 + `unpacman then reads with it, and ${PROGRAMS[provider]} elsewhere on this Mac — `
                 + `in Terminal, say — keeps the sign-in it has.${offered}`);
}

function anotherAccount(provider, a, root, onDone) {
  if (!confirmAnother(provider, a)) return;
  begin(`switch-${provider}`, root, onDone);
}

// The account an AI reads with, as its card says it (aiCard).
function shownAccount(accounts, provider) {
  const mine = accounts.filter(a => a.provider === provider);
  return mine.find(x => x.signed_in && !x.outdated) || mine.find(x => x.signed_in)
    || mine.find(x => x.installed) || mine[0] || null;
}

// Who unpacman reads with, for Advanced settings › This computer (2026-10-20):
// a line for Claude and for ChatGPT, where installed, each with Change
// account — the same switch as on the card here, for unpacman only — or Sign
// in. `onLeave` is called before the page moves here (it closes the dialog).
export function accountLines(now, { onLeave = () => {} } = {}) {
  const lines = [];
  for (const provider of ['claude', 'codex']) {
    const a = shownAccount(now?.ais || [], provider);
    if (!a?.installed) continue;
    const name = provider === 'claude' ? 'Claude' : 'ChatGPT';
    const said = a.signed_in
      ? [`${name}: ${a.who || 'signed in'}`, a.plan ? ` · ${a.plan}` : '', a.own ? ', for unpacman only' : ''].join('')
      : `${name}: not signed in`;
    const act = a.signed_in
      ? h('button', { class: 'linkish', type: 'button', text: 'Change account',
                      onclick: () => changeAccount(provider, { onLeave }) })
      : h('a', { class: 'linkish', href: '#/ready', text: 'Sign in', onclick: () => onLeave() });
    lines.push(h('div', { class: 'set-account', 'data-provider': provider },
      h('span', { class: 'set-help', text: said }), ' ', act));
  }
  if (!lines.length) {
    lines.push(h('div', { class: 'set-account' },
      h('span', { class: 'set-help', text: 'No AI is signed in yet.' }), ' ',
      h('a', { class: 'linkish', href: '#/ready', text: 'Getting ready', onclick: () => onLeave() })));
  }
  return lines;
}

// Change account from anywhere: asked as on the card, then started, and shown
// here as it goes — the sign-in page to open, a code box, Stop.
export async function changeAccount(provider, { onLeave = () => {} } = {}) {
  const now = cached || await state();
  const a = shownAccount(now?.ais || [], provider) || {};
  if (!confirmAnother(provider, a)) return;
  onLeave();
  try {
    const job = await api('POST', `/api/ready/switch-${provider}`);
    jobsShown.set(job.kind, job);
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  if (location.hash === '#/ready') window.dispatchEvent(new HashChangeEvent('hashchange'));
  else location.hash = '#/ready';
}

function macSignInAgain(provider, a, root, onDone) {
  const name = provider === 'claude' ? 'Claude' : 'ChatGPT';
  const program = PROGRAMS[provider];
  const own = a.who || 'unpacman\'s own account';
  if (!confirm(a.mac_signed_in
    ? `Stop reading with ${own}, and read with ${program}'s sign-in on this Mac `
      + `${a.mac_who ? `(${a.mac_who}) ` : ''}again?\n\nunpacman signs ${own} out; to read `
      + 'with it again, sign in to it again.'
    : `Stop reading with ${own}?\n\n${program} is not signed in on this Mac, so ${name} `
      + `will need a sign-in before it can read again. unpacman signs ${own} out.`)) return;
  begin(`revert-${provider}`, root, onDone);
}

// Whose sign-in Claude Code or Codex has elsewhere on the Mac, beside one of
// unpacman's own.
function elsewhere(provider, a) {
  return h('div', { class: 'ready-sub', text: `${PROGRAMS[provider]} elsewhere on this Mac `
    + (a.mac_signed_in ? `is signed in${a.mac_who ? ` as ${a.mac_who}` : ''}.` : 'is not signed in.') });
}

function aiCard(provider, accounts, now, root, onDone) {
  const mine = accounts.filter(a => a.provider === provider);
  const a = mine.find(x => x.signed_in && !x.outdated) || mine.find(x => x.signed_in)
    || mine.find(x => x.installed) || mine[0]
    || { provider, installed: false, signed_in: false };
  const name = provider === 'claude' ? 'Claude' : 'ChatGPT';
  const program = PROGRAMS[provider];
  // The job on screen: one running, else the last to end — a sign-in with
  // another account, going back to the Mac's, a sign-in, an install.
  const shown = ['switch', 'revert', 'signin', 'install'].map(k => jobsShown.get(`${k}-${provider}`));
  const job = shown.find(j => j?.state === 'running') || shown.find(Boolean);
  const lines = [];
  const actions = [];
  const macAgain = () => h('button', { class: 'linkish', type: 'button',
                                       text: 'Use this Mac\'s sign-in again',
                                       onclick: () => macSignInAgain(provider, a, root, onDone) });
  if (a.signed_in) {
    lines.push(['Signed in', a.who ? ` as ${a.who}` : '', a.plan ? ` · ${a.plan}` : '',
                a.own ? ', for unpacman only' : ''].join(''));
    if (a.own) lines.push(elsewhere(provider, a));
    if (a.note) lines.push(h('div', { class: 'set-error', text: a.note }));
    // Too old for the protections a reading runs under: one click updates it.
    if (a.outdated && now.mac) {
      actions.push(h('button', { class: 'primary', type: 'button', text: `Update ${program}`,
                                 onclick: () => begin(`install-${provider}`, root, onDone) }));
    }
    actions.push(h('button', { class: 'linkish', type: 'button', text: 'Change account',
                               onclick: () => anotherAccount(provider, a, root, onDone) }));
    if (a.own) actions.push(macAgain());
  } else if (a.installed && a.own) {
    // unpacman's own account has been signed out — at claude.ai, say.
    lines.push(`unpacman's own ${name} account is signed out.`);
    lines.push(elsewhere(provider, a));
    actions.push(h('button', { class: 'primary', type: 'button', text: `Sign in to ${name}`,
                               onclick: () => begin(`signin-${provider}`, root, onDone) }));
    actions.push(macAgain());
  } else if (a.installed) {
    lines.push(`${program} is installed, but not signed in.`);
    actions.push(h('button', { class: 'primary', type: 'button', text: `Sign in to ${name}`,
                               onclick: () => begin(`signin-${provider}`, root, onDone) }));
  } else {
    lines.push(`${program}, which unpacman reads with, is not installed.`);
    if (now.mac) {
      actions.push(h('button', { class: 'primary', type: 'button', text: `Install ${program}`,
                                 onclick: () => begin(`install-${provider}`, root, onDone) }));
      lines.push(h('div', { class: 'ready-sub',
        text: provider === 'claude' ? 'From Anthropic, into your own folder: about 100 MB, no password.'
          : 'From OpenAI, into your own folder: about 135 MB, no password.' }));
    }
  }
  lines.push(h('div', { class: 'ready-sub', text: PLANS[provider] }));
  const usable = (x) => x.signed_in && !x.outdated;
  const other = accounts.some(x => x.provider !== provider && usable(x));
  return card({ title: name, state: usable(a), optional: other, lines, actions, job, root, onDone });
}

// The services read with by a key of one's own (openweight.py), each the way
// to read without a Claude or ChatGPT plan: DeepSeek's own (2026-10-13), and
// OpenRouter (2026-10-15), one account for Claude's models and many others.
// Both are paid in advance, and both read through Claude Code, which must be
// installed — but needs no Claude plan for them.
const money = (n) => `$${Number(n).toFixed(2)}`;
const HOSTS = {
  deepseek: {
    name: 'DeepSeek', placeholder: 'sk-…',
    keys: ['https://platform.deepseek.com/api_keys', 'platform.deepseek.com'],
    credit: 'and add credit there: DeepSeek is paid in advance.',
    price: 'Paid per use, with no plan — per token, a fraction of what Claude Opus costs.',
    // What the saved key has, as DeepSeek says it; and what stops it.
    has: (st) => (st.balance ? `; its balance is ${st.balance}` : ''),
    warn: (st) => (st.available === false
      ? 'DeepSeek says this key cannot be used for now — most likely its balance is spent. '
        + 'Top it up at platform.deepseek.com.' : ''),
  },
  openrouter: {
    name: 'OpenRouter', placeholder: 'sk-or-…',
    keys: ['https://openrouter.ai/keys', 'openrouter.ai/keys'],
    credit: 'and buy credit there: OpenRouter is paid in advance.',
    price: 'Paid per use, with no plan: one account for Claude’s models and many others, '
           + 'each at its own price per token.',
    // A limit set on the key, what is left of it; none, no limit.
    has: (st) => (st.remaining != null ? `; it may spend ${money(st.remaining)} more` : ''),
    warn: (st) => (st.free_tier
      ? 'OpenRouter says this account has never had credit, and a reading needs some: buy it '
        + 'at openrouter.ai/settings/credits.' : ''),
  },
};

function hostCard(host, accounts, now, root, onDone) {
  const { name, price } = HOSTS[host];
  const a = accounts.find(x => x.account === host) || { signed_in: false };
  const claude = accounts.find(x => x.provider === 'claude' && x.installed);
  const installing = jobsShown.get('install-claude')?.state === 'running';
  const lines = [];
  const actions = [];
  if (!claude) {
    lines.push(installing ? `Claude Code, which ${name} reads through, is being installed (see Claude, above).`
      : `${name} reads through Claude Code, which is not installed. No Claude plan is needed for it.`);
    if (now.mac && !installing) {
      actions.push(h('button', { class: 'ghost', type: 'button', text: 'Install Claude Code',
                                 onclick: () => begin('install-claude', root, onDone) }));
    }
  }
  lines.push(hostKey(host, async () => paint(root, await state({ fresh: true }), onDone)));
  lines.push(h('div', { class: 'ready-sub', text: price }));
  const other = accounts.some(x => x.account !== host && x.signed_in && !x.outdated);
  return card({ title: name, state: !!a.signed_in, optional: other, lines, actions,
                root, onDone });
}

// What each service said of its saved key, kept a minute: this page is drawn
// again every second while something installs, and it need not be asked each time.
const hostSaid = {};
function hostState(host) {
  const kept = hostSaid[host];
  if (!kept || Date.now() - kept.at > 60_000) {
    const said = { at: Date.now(), value: null };
    said.answer = api('GET', `/api/open/${host}`).then((v) => { said.value = v; return v; });
    said.answer.catch(() => { if (hostSaid[host] === said) delete hostSaid[host]; });
    hostSaid[host] = said;
  }
  return hostSaid[host];
}

// A service's key, here and in Advanced settings: kept once the service has
// said it is one, shown only by its last characters, with what the service
// says of it. `changed()` is told when it is saved or removed.
// The services whose saved key is being replaced: a key of another account
// (2026-10-17). Kept here, since this page is drawn again every second while
// something installs, and the box must stay open through that.
const replacing = new Set();

function hostKey(host, changed = () => {}) {
  const { name, placeholder, keys, credit, has, warn } = HOSTS[host];
  const box = h('div', { class: 'ds-key', 'data-host': host }, h('span', { class: 'spin' }), ' Looking…');
  const tell = () => {
    delete hostSaid[host];
    replacing.delete(host);
    window.dispatchEvent(new Event('unpacman:ready'));
    changed();
  };
  // The box to paste a key in: the first one, or one replacing the key saved
  // — which stays until the service has accepted the new one.
  const form = (st) => {
    const key = h('input', { type: 'password', autocomplete: 'off', spellcheck: 'false',
                             placeholder, 'aria-label': `${name} key`, class: 'ds-key-in',
                             'data-keep': `${host}-key` });
    const msg = h('div', { class: 'ready-sub', role: 'status' });
    const save = h('button', { class: 'primary', type: 'button', text: 'Save the key' });
    const keep = async () => {
      if (save.disabled || !key.value.trim()) return;
      save.disabled = true;
      msg.className = 'ready-sub';
      mount(msg, h('span', { class: 'spin' }), ` Checking it with ${name}…`);
      try {
        await api('PUT', '/api/open/key', { host, key: key.value });
        tell();
      } catch (err) {
        msg.className = 'set-error';
        msg.textContent = err.message;
        save.disabled = false;
      }
    };
    save.addEventListener('click', keep);
    key.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); keep(); } });
    mount(box,
      st.key ? h('div', { class: 'ready-line' }, `A key ending ${st.hint} is saved. Paste the `
        + `other one: it replaces this one once ${name} accepts it. `,
        h('button', { class: 'linkish', type: 'button', text: 'Cancel',
                      onclick: () => { replacing.delete(host); show(st); } })) : null,
      h('div', { class: 'ds-key-row' }, key, save), msg,
      h('div', { class: 'ready-sub' }, 'Make a key at ',
        h('a', { href: keys[0], target: '_blank', rel: 'noopener', text: keys[1] }),
        ` ${credit} The key is kept on this computer only.`));
  };
  const show = (st) => {
    if (!st.key || replacing.has(host)) {
      form(st);
      return;
    }
    const stop = warn(st);
    mount(box,
      h('div', { class: 'ready-line' }, `A key ending ${st.hint} is saved`, has(st), '. ',
        h('button', { class: 'linkish', type: 'button', text: 'Use another key',
                      onclick: () => { replacing.add(host); form(st); } }), ' · ',
        h('button', { class: 'linkish', type: 'button', text: 'Remove the key',
          onclick: async () => {
            if (!confirm(`Remove the ${name} key from this computer?`)) return;
            await api('DELETE', `/api/open/key?host=${host}`).catch(() => null);
            tell();
          } })),
      stop ? h('div', { class: 'set-error', text: stop }) : null,
      st.error ? h('div', { class: 'set-error', text: st.error }) : null);
  };
  // Known already (this page is drawn again every second while something
  // installs): shown at once, without a moment of "Looking…".
  const said = hostState(host);
  if (said.value) show(said.value);
  else said.answer.then(show, (err) => mount(box, h('div', { class: 'set-error', text: err.message })));
  return box;
}

export function deepseekKey(changed = () => {}) { return hostKey('deepseek', changed); }
export function openrouterKey(changed = () => {}) { return hostKey('openrouter', changed); }

function texCard(now, root, onDone) {
  const t = now.tex;
  const job = jobsShown.get('install-latex');
  const lines = [];
  const actions = [];
  if (t.ready) {
    lines.push(t.trial === 'ok' ? `${t.label} — it has typeset a test page.`
      : `${t.label} is installed.`);
  } else if (t.found && t.missing.length) {
    lines.push(`${t.label} is installed, but lacks ${t.missing.length} of the pieces the companion `
      + `needs: ${t.missing.slice(0, 5).join(', ')}${t.missing.length > 5 ? '…' : ''}.`);
  } else if (t.found && t.trial === 'failed') {
    lines.push(`${t.label} could not typeset a test page: ${t.trial_error}`);
  } else {
    lines.push('LaTeX turns the companion into a properly typeset PDF. A reading cannot finish without it.');
  }
  if (!t.ready && now.mac) {
    actions.push(h('button', { class: 'primary', type: 'button',
      text: t.found && t.ours ? 'Install the missing pieces' : 'Install LaTeX',
      onclick: () => begin('install-latex', root, onDone) }));
    lines.push(h('div', { class: 'ready-sub',
      text: t.found && !t.ours
        ? 'This installs a small, separate LaTeX (TinyTeX) for unpacman — about 330 MB in your '
          + 'own Library folder, no password. Your own LaTeX is not touched.'
        : 'TinyTeX, a small LaTeX: about 330 MB in your own Library folder, no password. '
          + 'It takes a few minutes.' }));
  }
  return card({ title: 'LaTeX, to typeset the companion', state: t.ready, lines, actions,
                job, root, onDone });
}

function paint(root, now, onDone) {
  if (!now) return;
  // What is being typed here survives the page being drawn again, which it is
  // every second while something installs: its value, and the keyboard.
  const typed = new Map([...root.querySelectorAll('input[data-keep]')]
    .map(input => [input.dataset.keep, input.value]));
  const active = document.activeElement;
  const focused = root.contains(active) ? active.dataset?.keep : null;
  const caret = focused ? [active.selectionStart, active.selectionEnd] : null;
  draw(root, now, onDone);
  root.querySelectorAll('input[data-keep]').forEach((input) => {
    if (typed.get(input.dataset.keep)) input.value = typed.get(input.dataset.keep);
    if (input.dataset.keep === focused) {
      input.focus({ preventScroll: true });
      try { input.setSelectionRange(...caret); } catch { /* not a text field */ }
    }
  });
}

function draw(root, now, onDone) {
  const accounts = now.ais || [];
  const anyAi = accounts.some(a => a.signed_in && !a.outdated);
  const busy = [...jobsShown.values()].some(j => j.state === 'running');
  const head = now.ok
    ? 'Everything a reading needs is in place.'
    : 'Before the first reading, unpacman needs two things on this computer: an AI to read '
      + 'with, and LaTeX to typeset what it writes. Each takes one click here, and nothing on '
      + 'this page costs money.';
  mount(root, h('section', { class: 'stage ready' },
    h('h1', { text: 'Getting ready' }),
    h('p', { class: 'ready-lede', text: head }),
    h('h2', { class: 'ready-h' }, h('span', { class: 'ready-n', text: '1' }), 'An AI to read with',
      h('span', { class: 'ready-h-note', text: anyAi ? '' : ' — any one of these is enough' })),
    h('div', { class: 'ready-cards' }, aiCard('claude', accounts, now, root, onDone),
      aiCard('codex', accounts, now, root, onDone), hostCard('deepseek', accounts, now, root, onDone),
      hostCard('openrouter', accounts, now, root, onDone)),
    // Another way still, for those who want none of these (openweight.py).
    h('p', { class: 'ready-sub', text: 'Or an open-weight model on this Mac, with Ollama, which needs '
             + 'Claude Code installed but no subscription, and costs nothing: see Other ways to '
             + 'read, in Advanced settings.' }),
    h('h2', { class: 'ready-h' }, h('span', { class: 'ready-n', text: '2' }), 'Typesetting'),
    texCard(now, root, onDone),
    h('h2', { class: 'ready-h' }, h('span', { class: 'ready-n', text: '3' }), 'Reading PDFs'),
    card({ title: 'Reading the paper’s text', state: true,
           lines: [now.pdf === 'PDFium' ? 'Built in.' : `Built in, using ${now.pdf}.`],
           root, onDone }),
    !now.mac ? h('p', { class: 'ready-sub',
      text: 'Installing from this page works on a Mac. Here, install what is missing as the '
            + 'README describes, then press Check again.' }) : null,
    h('div', { class: 'ready-foot' },
      // Never held back by a job: it carries on without this page, which
      // picks it up again on return.
      h('button', { class: 'primary', type: 'button',
                    text: now.ok ? 'Continue' : 'Look around first',
                    onclick: () => { stopPolling(); onDone(); } }),
      h('button', { class: 'linkish', type: 'button', text: 'Check again',
                    disabled: busy || null,
                    onclick: async () => paint(root, await state({ fresh: true }), onDone) }),
      h('span', { class: 'ready-foot-note',
        text: now.ok ? '' : 'You can look around and add papers now; a reading starts once both are ready.' }))));
}

const AI_NAMES = { claude: 'Claude', codex: 'ChatGPT', open: 'open-weight models' };

// For Advanced settings: one line, and the way to the page.
export function summary(now) {
  if (!now) return 'Not checked yet.';
  if (now.ok) {
    // A host with a name of its own (DeepSeek, OpenRouter) is called by it.
    const named = (a) => (a.provider === 'open' && a.name && a.name !== 'Open-weight' ? a.name
      : AI_NAMES[a.provider] || a.name);
    const ai = [...new Set((now.ais || []).filter(a => a.signed_in && !a.outdated).map(named))];
    const list = ai.length > 1 ? `${ai.slice(0, -1).join(', ')} and ${ai[ai.length - 1]}` : ai[0];
    return `Ready: ${list}; ${now.tex.label} for typesetting.`;
  }
  return (now.blocking || []).join(' ') || 'Something is still missing.';
}

export function forget() { cached = null; }

// The Start button's own check, against the AI chosen and LaTeX: the reason
// it cannot start yet, said plainly, or "".
// `latex: false` for what typesets nothing — a search.
export function blocker(group, menuReady, { latex = true } = {}) {
  if (group && group.signed_in === false) {
    // The server says what this AI lacks; an older one only that it is not signed in.
    return group.not_ready || `${group.provider === 'open' ? group.label
      : AI_NAMES[group.provider] || group.label} is not signed in on this computer.`;
  }
  if (latex && menuReady && menuReady.tex === false) {
    return 'LaTeX, which typesets the companion, is not ready on this computer.';
  }
  return '';
}
