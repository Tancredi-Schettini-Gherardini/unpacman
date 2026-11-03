/* Send feedback: an email to the people who make unpacman, written for the
 * reader in their own email program, with a report on this copy attached if
 * they agree (server.py: /api/feedback, /api/report).
 *
 * unpacman sends nothing by itself. "Open in my email" is a mailto: link —
 * the reader sees the message there, can change it, and sends it or not — and
 * "Copy the message" is for anyone whose email lives in a browser, where such
 * a link may open nothing at all. */

import { api, authFetch, h, toast } from './dom.js';
import * as diag from './diag.js';
import { copyKey, here } from './place.js';

const KINDS = {
  problem: { label: 'Something went wrong', subject: 'unpacman: something went wrong',
             placeholder: 'What were you doing, and what happened? What did you expect instead?' },
  idea: { label: 'A suggestion', subject: 'unpacman: a suggestion',
          placeholder: 'What would make unpacman better for you?' },
  other: { label: 'Something else', subject: 'unpacman: feedback',
           placeholder: 'Your message' },
};

// A mailto: link longer than this may be dropped on its way to an email
// program — one that lives in a browser most of all — so a long report is cut
// to fit, from the top of its log first; "Copy the message" still has it all.
const LINK_LIMIT = 8000;
const REPORT_LOG = 30;
const DRAFT = 'unpacman.feedback';

let address = null;

// Kept for the session: a message is not lost to a dialog closed by mistake.
function draft(value) {
  try {
    if (value === undefined) return JSON.parse(sessionStorage.getItem(DRAFT) || 'null');
    if (value === null) sessionStorage.removeItem(DRAFT);
    else sessionStorage.setItem(DRAFT, JSON.stringify(value));
  } catch { /* private browsing: nothing kept */ }
  return null;
}

// Which screen the reader was on, in words — never which paper.
function where() {
  const hash = location.hash;
  if (hash === '#/ready') return 'Getting ready';
  const m = hash.match(/^#\/p\/[^/]+(?:\/([a-z]+))?/);
  if (!m) return 'the home page';
  if (m[1] === 'runs') return 'a paper’s list of runs';
  if (m[1] === 'paper') return 'the paper itself';
  if (m[1] === 'side') return 'a companion beside its paper';
  if (document.querySelector('#paper .sec')) return 'a companion';
  if (document.querySelector('#r-steps')) return 'a reading’s progress';
  return 'a paper';
}

// Home folders out of anything quoted, as the report does.
const tidy = (text) => String(text || '').replace(/\/Users\/[^/\s]+/g, '~');

function compose({ message, about, report }) {
  const lines = [message.trim(), '', '--', `Written with unpacman’s Send feedback, on ${where()}.`];
  if (about) lines.push(`What unpacman said: ${tidy(about)}`);
  if (report) lines.push('', report.trimEnd());
  return `${lines.join('\n')}\n`;
}

function link(to, subject, body) {
  const enc = (t) => encodeURIComponent(t.replace(/\r?\n/g, '\r\n'));
  return `mailto:${to}?subject=${enc(subject)}&body=${enc(body)}`;
}

// The link, with as much of the report as fits: the log's oldest lines go
// first, then the log, then the end of the report itself.
function fitted(to, subject, parts) {
  let url = link(to, subject, compose(parts));
  if (url.length <= LINK_LIMIT || !parts.report) return url;
  const cut = '\n(Cut to fit in an email: “Copy the message” in Send feedback gives all of it.)';
  const [head, log = ''] = parts.report.split(/\n(?=Log \(the last lines\):)/);
  const logLines = log.split('\n');
  while (logLines.length > 2) {
    logLines.splice(1, 1);
    url = link(to, subject, compose({ ...parts, report: `${head}\n${logLines.join('\n')}${cut}` }));
    if (url.length <= LINK_LIMIT) return url;
  }
  let short = head;
  while (short.length > 200) {
    short = short.slice(0, Math.floor(short.length * 0.8));
    url = link(to, subject, compose({ ...parts, report: `${short}…${cut}` }));
    if (url.length <= LINK_LIMIT) return url;
  }
  return link(to, subject, compose({ ...parts, report: '' }));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/* The dialog. `kind` chooses the subject; `about` is what unpacman itself said
 * where the reader came from (a reading that stopped, an install that failed),
 * quoted in the message. */
export async function open({ kind = 'problem', about = '' } = {}) {
  document.querySelector('dialog.feedback')?.remove();
  if (!address) address = (await api('GET', '/api/feedback').catch(() => null))?.to || null;

  let report = null;
  // The server's report, with what this page noticed put before its log —
  // the log is what is cut first when the message is too long for a link.
  const reportReady = authFetch(`/api/report?log=${REPORT_LOG}`)
    .then(r => (r.ok ? r.text() : ''))
    .catch(() => '')
    .then((text) => {
      const seen = diag.lines();
      if (seen) {
        const at = text.search(/\n(?=Log \(the last lines\):)/);
        text = at < 0 ? `${text.trimEnd()}\n\n${seen}\n`
          : `${text.slice(0, at).trimEnd()}\n\n${seen}\n${text.slice(at)}`;
      }
      report = text;
      return text;
    });

  const kept = draft();
  if (kept && KINDS[kept.kind] && !about) kind = kept.kind;

  const text = h('textarea', {
    class: 'fb-text', rows: '6', maxlength: '4000', 'aria-label': 'Your message',
    placeholder: KINDS[kind].placeholder,
  });
  text.value = kept?.message || '';
  const kinds = h('div', { class: 'fb-kinds', role: 'radiogroup', 'aria-label': 'What it is about' },
    Object.entries(KINDS).map(([id, k]) => h('label', { class: 'fb-kind' },
      h('input', { type: 'radio', name: 'fb-kind', value: id, checked: id === kind,
                   onchange: () => { kind = id; text.placeholder = k.placeholder; touched(); } }),
      h('span', { text: k.label }))));

  const attach = h('input', { type: 'checkbox', checked: kept ? kept.attach !== false : true });
  const shown = h('pre', { class: 'fb-report', text: 'Getting it…' });
  const details = h('details', { class: 'fb-see' },
    h('summary', { text: 'See what it says' }), shown);
  reportReady.then((t) => { shown.textContent = t || 'The report could not be made.'; });

  const send = h('a', { class: 'primary fb-send', href: '#', text: 'Open in my email' });
  const copy = h('button', { class: 'ghost', type: 'button', text: 'Copy the message' });
  const after = h('p', { class: 'fb-after', role: 'status' });
  let used = false;

  const parts = () => ({ message: text.value, about, report: attach.checked ? report || '' : '' });
  // Kept in step with every keystroke: the link is followed by the browser
  // itself, which no browser stops as it may stop a page opening a program.
  function touched() {
    draft({ kind, message: text.value, attach: attach.checked });
    const empty = !text.value.trim();
    send.classList.toggle('off', empty);
    send.setAttribute('aria-disabled', String(empty));
    copy.disabled = empty;
    send.href = empty || !address ? '#' : fitted(address, KINDS[kind].subject, parts());
    if (empty && !used) after.textContent = 'Write a few words above, then choose how to send them.';
    else if (!used) after.textContent = '';
  }
  text.addEventListener('input', touched);
  attach.addEventListener('change', touched);
  // The report arrives a moment after the dialog opens; the link waits for it.
  reportReady.then(() => { if (dialog.isConnected) touched(); });

  const sentHint = () => {
    used = true;
    after.textContent = '';
    after.append('Your email program should now show the message: press Send there. If nothing '
      + 'opened, press “Copy the message” and paste it into an email to ', h('b', { text: address }), '.');
  };
  send.addEventListener('click', (e) => {
    if (send.classList.contains('off') || !address) { e.preventDefault(); text.focus(); return; }
    // The report is in the link only once it has come: a click before then
    // waits for it, then follows the link itself.
    if (attach.checked && report === null) {
      e.preventDefault();
      reportReady.then(() => { touched(); location.href = send.href; sentHint(); });
      return;
    }
    setTimeout(sentHint, 300);
  });
  copy.addEventListener('click', async () => {
    if (attach.checked && report === null) await reportReady;
    const whole = compose(parts());
    if (await copyText(whole)) {
      used = true;
      after.textContent = '';
      after.append('Copied. Paste it into an email to ', h('b', { text: address || 'us' }),
        ` — with a subject such as “${KINDS[kind].subject}”.`);
      return;
    }
    // No clipboard here: the message, selected, to copy by hand.
    const area = h('textarea', { class: 'method-text report-text', readonly: true,
                                 'aria-label': 'The message' });
    area.value = whole;
    body.replaceChildren(
      h('h2', { id: 'feedback-title', text: 'Your message' }),
      h('p', { class: 'set-intro' }, `It is selected: press ${copyKey} to copy it, then paste it into an `
        + 'email to ', h('b', { text: address || 'us' }), '.'),
      area,
      h('div', { class: 'set-foot' }, h('span'),
        h('button', { class: 'primary', type: 'button', text: 'Done', onclick: () => dialog.close() })));
    area.focus();
    area.select();
  });

  const body = h('div', { class: 'settings-form' },
    h('h2', { id: 'feedback-title', text: 'Send feedback' }),
    h('p', { class: 'set-intro', text: 'Tell us what went wrong, what was confusing, or what you '
      + 'would like unpacman to do. It goes by email to the people who make unpacman; nothing '
      + 'is sent until you send it yourself.' }),
    kinds,
    text,
    about ? h('p', { class: 'fb-about' }, 'It will quote what unpacman said: ',
                h('span', { text: tidy(about) })) : null,
    h('label', { class: 'set-check fb-attach' }, attach,
      h('span', {}, h('b', { text: 'Attach a report on this copy of unpacman' }),
        ` — its version, how ${here} is set up, and recent error messages. Never your papers `
        + 'or their titles.')),
    details,
    address
      ? h('p', { class: 'fb-to' }, 'To ', h('b', { text: address }), ' ',
          h('button', { class: 'linkish', type: 'button', text: 'Copy the address',
                        onclick: async () => toast(await copyText(address)
                          ? 'Address copied.' : address) }))
      : h('p', { class: 'set-error', text: 'unpacman could not say where feedback goes. '
          + 'Reload the page and try again.' }),
    h('div', { class: 'set-foot fb-foot' },
      h('button', { class: 'linkish', type: 'button', text: 'Close', onclick: () => dialog.close() }),
      h('span', { class: 'fb-actions' }, copy, send)),
    after);

  const dialog = h('dialog', { class: 'settings feedback', 'aria-labelledby': 'feedback-title' }, body);
  dialog.addEventListener('close', () => {
    // Sent, or copied to send: the next one starts afresh.
    if (used) draft(null);
    dialog.remove();
  });
  document.body.append(dialog);
  dialog.showModal();
  touched();
  text.focus();
}

// For the bar on every screen.
export function button() {
  return h('button', {
    class: 'ghost fb-button', type: 'button', id: 'open-feedback',
    title: 'Tell us about a problem, or an idea', 'aria-label': 'Send feedback',
    onclick: () => open(),
  },
  h('span', { class: 'fb-icon', 'aria-hidden': 'true',
              html: '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M2.6 3h10.8c.6 0 1.1.5 1.1 1.1v6.3c0 .6-.5 1.1-1.1 1.1H7.2L4.3 14v-2.5H2.6c-.6 0-1.1-.5-1.1-1.1V4.1C1.5 3.5 2 3 2.6 3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>' }),
  h('span', { class: 'fb-label', text: 'Feedback' }));
}
