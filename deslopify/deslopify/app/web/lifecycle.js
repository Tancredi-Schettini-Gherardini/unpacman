/* This copy of unpacman: its version and updates, quitting it, and a report
 * to send whoever helps (updates.py, server.py). */

import { api, authFetch, bytes, confirmAction, h, mount, toast } from './dom.js';
import * as ready from './ready.js';
import * as feedback from './feedback.js';
import * as signup from './news.js';
import * as about from './about.js';
import { copyKey, onLinux, onWindows } from './place.js';

let last = null;              // the last /api/update answer

export async function updateStatus({ check = false } = {}) {
  try { last = await api('GET', `/api/update${check ? '?check=1' : ''}`); }
  catch { /* an old server, or none: no update to offer */ }
  return last;
}

function day(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

export function versionText(status) {
  const v = status?.version;
  return v ? `${day(v.date)} (${v.short})` : 'not a copy that can update itself';
}

function news(status) {
  const items = status?.news || [];
  if (!items.length) return null;
  return h('ul', { class: 'update-news' },
    items.map(n => h('li', {}, h('span', { text: n.subject }),
      h('span', { class: 'update-news-when', text: ` · ${day(n.date)}` }))));
}

// On the home page, when a new version is waiting and can be taken.
export function banner(status) {
  if (!status?.available) return null;
  const n = status.behind;
  const list = news(status);
  if (list) list.hidden = true;
  const more = list ? h('button', { class: 'linkish', type: 'button', text: 'What is new',
    onclick: () => { list.hidden = !list.hidden; more.textContent = list.hidden ? 'What is new' : 'Hide'; } }) : null;
  return h('div', { class: 'update-banner', role: 'status' },
    h('div', { class: 'update-banner-row' },
      h('span', { text: `A new version of unpacman is ready${n > 1 ? ` (${n} changes)` : ''}.` }),
      more,
      h('button', { class: 'primary', type: 'button', text: 'Update now', onclick: () => runUpdate() })),
    list);
}

// Update, restart, and come back on the new version. A modal, because the
// page is about to be replaced under the reader's feet.
export async function runUpdate() {
  let status;
  try { status = await api('POST', '/api/update'); }
  catch (err) { toast(err.message, 'err'); return; }
  const before = await api('GET', '/api/hello').catch(() => null);

  const step = h('p', { class: 'update-step' }, h('span', { class: 'spin' }), ' ', status.step || 'Starting…');
  const dialog = h('dialog', { class: 'settings update-dialog', 'aria-labelledby': 'update-title' },
    h('div', { class: 'settings-form' },
      h('h2', { id: 'update-title', text: 'Updating unpacman' }),
      h('p', { class: 'set-intro',
               text: 'Your papers, readings and settings are kept as they are. This takes a minute or two.' }),
      step));
  dialog.addEventListener('cancel', (e) => e.preventDefault());
  document.body.append(dialog);
  dialog.showModal();

  const fail = (message) => {
    mount(dialog.querySelector('.settings-form'),
      h('h2', { id: 'update-title', text: 'The update did not finish' }),
      h('p', { class: 'set-error', text: message }),
      h('div', { class: 'set-foot' },
        h('button', { class: 'linkish', type: 'button', text: 'Tell us about it',
                      onclick: () => { dialog.close(); dialog.remove();
                                       feedback.open({ kind: 'problem', about: message }); } }),
        h('button', { class: 'primary', type: 'button', text: 'Close',
                      onclick: () => { dialog.close(); dialog.remove(); } })));
  };

  // Follow the update until the server goes down to restart…
  for (;;) {
    await new Promise(r => setTimeout(r, 900));
    let now;
    try { now = await api('GET', '/api/update'); } catch { break; }
    mount(step, h('span', { class: 'spin' }), ' ', now.step || 'Working…');
    if (now.state === 'failed') { fail(now.error || 'Something went wrong.'); return; }
    if (now.state === 'restarting') break;
  }
  // …then wait for the new version to answer, and load the page it serves.
  mount(step, h('span', { class: 'spin' }), ' Restarting…');
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000));
    const hello = await api('GET', '/api/hello').catch(() => null);
    if (hello && (!before || hello.started !== before.started)) {
      location.reload();
      return;
    }
  }
  fail('unpacman has not come back after restarting. Open it again from its icon; if that '
       + 'does not work, “Copy a report” in Advanced settings says what happened.');
}

// Stop unpacman — asking first if a reading would stop with it.
export async function quit() {
  let answer;
  try { answer = await api('POST', '/api/quit', {}); }
  catch (err) {
    if (!/reading/.test(err.message)) { toast(err.message, 'err'); return; }
    if (!confirm(`${err.message} Quit anyway?`)) return;
    try { answer = await api('POST', '/api/quit', { force: true }); }
    catch (err2) { toast(err2.message, 'err'); return; }
  }
  document.querySelectorAll('dialog').forEach(d => { d.close?.(); d.remove(); });
  document.title = 'unpacman — stopped';
  mount(document.body, h('main', { class: 'stopped' },
    h('h1', { text: 'unpacman has stopped' }),
    h('p', { text: answer?.background
      ? (onLinux
        ? 'To open it again, choose Unpacman in your applications menu (search for “unpacman”).'
        : 'To open it again, double-click Unpacman in your Applications folder — or press ⌘ Space, '
          + 'type “unpacman”, and press Return.')
      : onWindows ? 'To open it again, choose Unpacman in the Start menu (type “unpacman”).'
        : 'To open it again, start it the way you started it before.' }),
    h('p', { class: 'stopped-sub', text: 'You can close this tab.' })));
}

// "Copy a report": the text goes straight to the clipboard. Safari allows
// that only within the click itself, so the clipboard is handed the request
// while it is still running rather than its answer afterwards.
export async function copyReport() {
  const text = authFetch('/api/report').then(r => (r.ok ? r.text() : Promise.reject(new Error(r.statusText))));
  try {
    if (window.ClipboardItem && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/plain': text.then(t => new Blob([t], { type: 'text/plain' })) })]);
    } else {
      await navigator.clipboard.writeText(await text);
    }
    toast('Copied. Paste it into an email to whoever helps you.');
    return;
  } catch { /* shown instead, to copy by hand */ }
  let body = '';
  try { body = await text; } catch (err) { toast(err.message, 'err'); return; }
  const area = h('textarea', { class: 'method-text report-text', readonly: true,
                               'aria-label': 'The report' });
  area.value = body;
  const dialog = h('dialog', { class: 'settings wide', 'aria-labelledby': 'report-title' },
    h('div', { class: 'settings-form' },
      h('h2', { id: 'report-title', text: 'A report on this copy of unpacman' }),
      h('p', { class: 'set-intro', text: `It is selected: press ${copyKey} to copy it, then paste it into an email.` }),
      area,
      h('div', { class: 'set-foot' }, h('span'),
        h('button', { class: 'primary', type: 'button', text: 'Done',
                      onclick: () => { dialog.close(); dialog.remove(); } }))));
  document.body.append(dialog);
  dialog.showModal();
  area.focus();
  area.select();
}

// The part of Advanced settings about this copy: getting ready, the version,
// the report, quitting. `close` closes the settings dialog.
export function section(close) {
  const readyLine = h('span', { class: 'set-help', text: 'Checking…' });
  // Who unpacman reads with, and Change account (ready.accountLines).
  const accountsBox = h('span', { class: 'set-accounts' }, h('span', { class: 'set-help', text: 'Checking…' }));
  ready.state({ fresh: true }).then(now => {
    readyLine.textContent = ready.summary(now);
    mount(accountsBox, ready.accountLines(now, { onLeave: close }));
  });

  const versionLine = h('span', { class: 'set-help', text: '…' });
  const updateBox = h('div', { class: 'set-update' });
  const paintUpdate = (s) => {
    versionLine.textContent = versionText(s);
    // A copy from a package has nothing to look for online (updates.py).
    check.hidden = !!s?.package;
    const bits = [];
    if (!s) bits.push(h('p', { class: 'set-help-block', text: 'Updates could not be checked.' }));
    else if (s.state === 'checking') bits.push(h('p', { class: 'set-help-block' }, h('span', { class: 'spin' }), ' Checking…'));
    else if (s.available) {
      bits.push(h('p', { class: 'set-ok', text: `A new version is ready${s.behind > 1 ? ` (${s.behind} changes)` : ''}.` }),
        news(s),
        h('button', { class: 'primary', type: 'button', text: 'Update now', onclick: () => { close(); runUpdate(); } }));
    } else if (s.why_not) bits.push(h('p', { class: 'set-help-block', text: s.why_not }));
    else if (s.error) bits.push(h('p', { class: 'set-error', text: s.error }));
    else if (s.checked) bits.push(h('p', { class: 'set-help-block', text: 'This is the newest version.' }));
    mount(updateBox, bits);
  };
  const check = h('button', { class: 'linkish', type: 'button', text: 'Check for a new version',
    onclick: async () => {
      check.disabled = true;
      paintUpdate({ ...(last || {}), state: 'checking' });
      paintUpdate(await updateStatus({ check: true }));
      check.disabled = false;
    } });
  updateStatus().then(paintUpdate);

  // Room taken, and the earlier runs of every paper given back (removal.py).
  const spaceLine = h('span', { class: 'set-help', text: 'Measuring…' });
  const tidyButton = h('button', { class: 'linkish', type: 'button', hidden: true });
  const paintSpace = async () => {
    let space;
    try { space = await api('GET', '/api/space'); }
    catch { spaceLine.textContent = 'Could not be measured.'; return; }
    spaceLine.textContent = `${space.papers} paper${space.papers === 1 ? '' : 's'} and `
      + `${space.runs} run${space.runs === 1 ? '' : 's'} take ${bytes(space.bytes)}.`;
    const spare = space.earlier || { count: 0, bytes: 0 };
    tidyButton.hidden = !spare.count;
    tidyButton.textContent = `Delete the earlier runs of every paper (${spare.count}, ${bytes(spare.bytes)})`;
    tidyButton.onclick = async () => {
      const ok = await confirmAction({
        title: `Delete ${spare.count} earlier run${spare.count === 1 ? '' : 's'}?`,
        body: [
          'For every paper, this keeps the companion you read at each level, and any reading '
            + 'still going, and deletes every other run — earlier companions, and runs that '
            + `stopped or failed — freeing ${bytes(spare.bytes)}. The papers stay, and so do your `
            + 'explanations.',
          'This cannot be undone.',
        ],
        yes: 'Delete them', danger: true,
      });
      if (!ok) return;
      tidyButton.disabled = true;
      try {
        const out = await api('POST', '/api/tidy');
        toast(`${out.deleted} run${out.deleted === 1 ? '' : 's'} deleted — ${bytes(out.freed)} freed.`
              + (out.skipped?.length ? ` Some could not be deleted: ${out.skipped.join(', ')}.` : ''));
        document.dispatchEvent(new CustomEvent('unpacman:deleted'));
      } catch (err) {
        toast(err.message, 'err');
      }
      tidyButton.disabled = false;
      paintSpace();
    };
  };
  paintSpace();

  return h('fieldset', { class: 'set-computer' },
    h('legend', { text: 'This computer' }),
    h('div', { class: 'set-field' }, h('span', { class: 'set-label', text: 'Status' }),
      h('span', {}, readyLine, ' ',
        h('a', { class: 'linkish', href: '#/ready', text: 'Getting ready', onclick: () => close() }))),
    h('div', { class: 'set-field' }, h('span', { class: 'set-label', text: 'Accounts' }), accountsBox),
    h('div', { class: 'set-field' }, h('span', { class: 'set-label', text: 'Version' }),
      h('span', {}, versionLine, ' ', check)),
    updateBox,
    h('div', { class: 'set-field' }, h('span', { class: 'set-label', text: 'Space' }),
      h('span', {}, spaceLine, ' ', tidyButton)),
    signup.settingsRow(),
    h('div', { class: 'set-row' },
      h('button', { class: 'ghost', type: 'button', text: 'Send feedback',
                    title: 'An email to the people who make unpacman, with a report if you like',
                    onclick: () => { close(); feedback.open(); } }),
      h('button', { class: 'ghost', type: 'button', text: 'Copy a report',
                    title: 'For whoever helps you: versions and what went wrong, never your papers',
                    onclick: () => copyReport() }),
      h('button', { class: 'ghost', type: 'button', text: 'Quit unpacman',
                    onclick: () => quit() }),
      h('button', { class: 'linkish', type: 'button', text: 'About unpacman',
                    title: 'What unpacman is, what it can reach, and its limits',
                    onclick: () => { close(); about.open(); } })));
}
