/* Hearing about new versions (news.py): asked once, on the home page, and
 * answered in Advanced settings afterwards.
 *
 * The address is filled in from the reader's own sign-in, but nothing is sent
 * until they press "Keep me posted" — and then only the address, for this
 * alone. Without a sign-up form, it goes as a two-line email their own email
 * program opens, as Send feedback's does. */

import { api, h, mount, toast } from './dom.js';

// The email link, followed as a click (an email program opens; the page stays).
function follow(link) {
  const a = h('a', { href: link, hidden: true });
  document.body.append(a);
  a.click();
  a.remove();
}

function sentNote(out, to) {
  if (out.via === 'form') {
    return h('p', { class: 'news-done', text: 'Thank you — we will write to '
                    + `${out.email} when a new version is out.` });
  }
  return h('p', { class: 'news-done' },
    'Your email program should now show a short message to us: press Send there, and we will '
    + 'write when a new version is out. If nothing opened, send a line to ',
    h('a', { href: `mailto:${to}`, text: to }), ` from ${out.email}.`);
}

/* The question, in `box` (a card on the home page, or a part of Advanced
 * settings). `onDone(answer)` hears "yes" or "no". */
function form(box, st, { onDone = () => {}, dismiss = true } = {}) {
  const input = h('input', { type: 'email', autocomplete: 'email', spellcheck: 'false',
                             placeholder: 'you@example.org', 'aria-label': 'Your email address' });
  input.value = st.suggestion || '';
  const msg = h('div', { class: 'news-msg', role: 'status' });
  const yes = h('button', { class: 'primary', type: 'submit', text: 'Keep me posted' });
  const no = dismiss ? h('button', { class: 'linkish', type: 'button', text: 'No, thanks' }) : null;
  const ask = h('form', { class: 'news-form' },
    h('div', { class: 'news-row' }, input, yes, no), msg);
  ask.addEventListener('submit', async (e) => {
    e.preventDefault();
    yes.disabled = true;
    try {
      const out = await api('POST', '/api/news', { email: input.value });
      if (out.link) follow(out.link);
      mount(box, sentNote(out, st.to));
      onDone('yes');
    } catch (err) {
      msg.textContent = err.message;
      yes.disabled = false;
    }
  });
  no?.addEventListener('click', async () => {
    await api('POST', '/api/news', { answer: 'no' }).catch(() => null);
    onDone('no');
  });
  return ask;
}

// The card on the home page, the first time — and never again once answered.
export async function maybeAsk(view) {
  let st;
  try { st = await api('GET', '/api/news'); } catch { return; }
  if (st.asked || !view.isConnected || view.querySelector('.news-card')) return;
  const card = h('section', { class: 'news-card', 'aria-labelledby': 'news-title' });
  mount(card,
    h('h2', { id: 'news-title', text: 'Hear about new versions?' }),
    h('p', { text: 'unpacman is new, and changes often. Leave your email address and we will '
             + 'write when a new version is out. That is all it is used for, and it is never '
             + 'shared.' }),
    st.suggestion ? h('p', { class: 'news-from',
      text: `Filled in from ${st.from}. Nothing is sent unless you press Keep me posted.` }) : null,
    form(card, st, { onDone: (answer) => {
      if (answer === 'no') {
        card.remove();
        toast('You can still sign up in Advanced settings.');
      }
    } }));
  const library = view.querySelector('#library');
  if (library) library.before(card);
  else view.append(card);
}

// A line of Advanced settings › This computer.
export function settingsRow() {
  const line = h('span', { class: 'set-help', text: '…' });
  const box = h('div', { class: 'news-settings' });
  const paint = async () => {
    let st;
    try { st = await api('GET', '/api/news'); } catch { line.textContent = ''; return; }
    if (st.answer === 'yes') {
      line.textContent = `We will email ${st.email} when a new version is out. `;
      mount(box, h('button', { class: 'linkish', type: 'button', text: 'Stop these emails',
        onclick: async () => {
          const out = await api('DELETE', '/api/news').catch(() => null);
          if (out?.link) follow(out.link);
          toast(out?.link ? 'Your email program should show a message asking us to stop: press Send there.'
                          : 'Stopped.');
          paint();
        } }));
    } else {
      line.textContent = 'Not signed up for news of new versions. ';
      mount(box, h('button', { class: 'linkish', type: 'button', text: 'Email me about new versions…',
        onclick: () => mount(box, form(box, st, { dismiss: false, onDone: () => {} })) }));
    }
  };
  paint();
  return h('div', { class: 'set-field' }, h('span', { class: 'set-label', text: 'News' }),
    h('span', {}, line, box));
}
