/* About unpacman: what it is for, and the two things anyone using it should
 * know first — what it can reach on their computer and what it sends where,
 * and that it makes the mistakes of the AI it reads with (2026-10-13). What
 * it is for is said in Tancredi's own words (2026-10-17), exactly as he gave
 * them: change it only at his word. */

import { api, h } from './dom.js';
import * as feedback from './feedback.js';
import { here } from './place.js';

// What unpacman is for, in Tancredi's words (2026-10-17).
export const ABOUT = 'Human understanding is a central purpose of mathematics, especially as we '
  + 'enter an era of industrial mathematical production through AI. We believe that a thoughtful '
  + 'and responsible use of this new technology can help with this goal. In contrast to the large '
  + 'number of technical resources dedicated to generating new results, Unpacman is designed to '
  + 'help understanding. Unpacman is a harness for navigating the literature at all scales, by '
  + 'writing structured summaries of one or multiple papers in an academic style. It offers '
  + 'various reader-friendly features depending on the user’s request, and its internal skill '
  + 'files are completely customizable. It is a tool but not a replacement for the process of '
  + 'assimilating mathematical knowledge.';

function day(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

export function open() {
  const version = h('p', { class: 'about-version', text: '' });
  const close = () => dialog.close();
  const dialog = h('dialog', { class: 'settings about', 'aria-labelledby': 'about-title' },
    h('div', { class: 'settings-form' },
      // The title takes the focus, so that the dialog opens at its beginning
      // rather than scrolled to its first button (on a phone, the last thing in it).
      h('h2', { id: 'about-title', text: 'About unpacman', tabindex: '-1', autofocus: true }),
      h('p', { class: 'about-lead', text: ABOUT }),
      h('h3', { text: 'Privacy' }),
      h('p', { text:
        `unpacman works through the AI programs on ${here} — Claude Code or Codex — signed in to `
        + 'your own account, so on this computer a reading can reach whatever those programs’ own '
        + 'settings allow. unpacman also keeps each reading to its own folder and to public '
        + 'websites: a safeguard, not a guarantee.' }),
      h('p', { text:
        'The paper, its companion, your questions and anything you write about your background '
        + 'are sent to the company that runs the AI you choose — Anthropic, OpenAI, DeepSeek or '
        + 'another — under its terms; through OpenRouter, to OpenRouter and to the company whose '
        + 'model it is. unpacman sends nothing to us unless you write to us.' }),
      h('h3', { text: 'Reliability' }),
      h('p', { text:
        'unpacman makes the same mistakes as the AI it reads with: it can misread a proof, miss '
        + 'a gap, or get a reference wrong — and say so confidently. Read a companion as you would '
        + 'a good student’s notes, not as a refereed paper: it says where it is unsure and what '
        + 'its own check found, and anything you rely on is worth checking in the paper itself.' }),
      version,
      h('div', { class: 'set-foot' },
        h('button', { class: 'linkish', type: 'button', text: 'Send feedback',
                      onclick: () => { close(); feedback.open(); } }),
        h('button', { class: 'primary', type: 'button', text: 'Close', onclick: close }))));
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) close(); });
  document.body.append(dialog);
  dialog.showModal();
  // Which unpacman this is: the version, as Advanced settings says it.
  api('GET', '/api/hello').then((hello) => {
    const v = hello?.version;
    if (v?.short) version.textContent = `This copy: version ${v.short}, of ${day(v.date)}.`;
  }).catch(() => null);
}

export function button() {
  return h('button', { class: 'ghost about-button', type: 'button', id: 'open-about',
                       text: 'About', title: 'What unpacman is, what it can reach, and its limits',
                       onclick: () => open() });
}
