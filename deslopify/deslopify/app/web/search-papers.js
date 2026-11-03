// Select PDFs from a completed search without changing individual imports or
// uploads. Fetching adds to the library; only the existing launch screen can
// start a paid reading. Keep the draft when returning from that screen.
import { api, h, mathText, mount, typeset } from './dom.js';

export function createSearchPapers({ slug, doc, max = 12, draft = {}, onContinue }) {
  const selected = draft.selected ||= new Set();
  const papers = draft.papers ||= new Map();
  const errors = draft.errors ||= new Map();
  const available = doc.results.flatMap((r, i) => r.pdf && doc.run_id ? [i] : []);
  const boxes = new Map();
  let busy = false, preparing = false, cancelled = false, disposed = false;
  let current = null, message = '';
  const panel = h('section', { class: 'search-papers', 'aria-label': 'Select papers to read' });
  const status = h('p', { class: 'search-papers-status', role: 'status', 'aria-live': 'polite' });
  const review = h('ul', { class: 'search-papers-review' });
  const here = () => !disposed && panel.isConnected;
  const picked = () => available.filter(i => selected.has(i));
  const ready = () => [...new Map(picked().filter(i => papers.has(i))
    .map(i => [papers.get(i).slug, papers.get(i)])).values()];
  const all = h('button', { class: 'ghost', type: 'button',
    text: available.length > max ? `Select first ${max} available` : 'Select all',
    onclick: () => {
      if (busy || preparing) return;
      selected.clear();
      available.slice(0, max).forEach(i => selected.add(i));
      message = ''; paint();
    } });
  const clear = h('button', { class: 'linkish', type: 'button', text: 'Clear selection',
    onclick: () => {
      if (busy || preparing) return;
      selected.clear(); errors.clear(); message = ''; paint();
    } });
  const fetch = h('button', { class: 'primary', type: 'button', text: 'Fetch selected papers',
    onclick: fetchSelected });
  const cancel = h('button', { class: 'ghost', type: 'button', text: 'Cancel downloads',
    onclick: () => {
      cancelled = true;
      message = 'Stopping after the current download. Papers already added will stay in your library.';
      paint();
    } });
  const next = h('button', { class: 'primary', type: 'button', onclick: async () => {
    if (busy || preparing || !here() || !ready().length) return;
    preparing = true; message = 'Opening run settings…'; paint();
    try { await onContinue(ready(), here); message = ''; }
    catch (err) { message = `Could not open run settings: ${err.message}`; }
    finally { preparing = false; if (here()) paint(); }
  } });
  mount(panel,
    h('h2', { text: 'Read selected papers together' }),
    h('p', { text: `Select up to ${max} papers below, fetch their PDFs, then review the selection. `
      + 'One paper gets its own companion; several get a collection companion. No reading starts here.' }),
    h('div', { class: 'search-papers-actions' }, all, clear, fetch, cancel, next), status, review);

  function paint() {
    const indices = picked(), got = ready();
    const waiting = indices.filter(i => !papers.has(i));
    for (const [i, box] of boxes) {
      box.checked = selected.has(i);
      box.disabled = busy || preparing || (!box.checked && selected.size >= max);
    }
    all.disabled = busy || preparing || !available.length;
    clear.disabled = busy || preparing || !indices.length;
    fetch.disabled = busy || preparing || !waiting.length;
    fetch.textContent = busy ? 'Fetching…'
      : waiting.some(i => errors.has(i)) ? 'Retry / fetch remaining papers' : 'Fetch selected papers';
    cancel.hidden = !busy;
    cancel.disabled = cancelled;
    next.hidden = !got.length;
    next.disabled = busy || preparing || !got.length;
    next.textContent = `Continue with ${got.length} ${got.length === 1 ? 'paper' : 'papers'}`;
    status.textContent = message || (indices.length
      ? `${indices.length} selected; ${got.length} distinct ${got.length === 1 ? 'paper' : 'papers'} ready. `
        + (waiting.length ? `${waiting.length} not yet fetched. ` : '')
        + 'Remove papers below or select more in the results. Only ready papers will be included.'
      : 'Select papers using the checkboxes on the results. Individual “Add to my papers” still works as before.');
    mount(review, ...indices.map(i => {
      const paper = papers.get(i);
      const label = i === current ? 'Fetching…' : paper
        ? paper.existing ? 'Already in your library' : 'Added to your library'
        : errors.has(i) ? `Could not fetch: ${errors.get(i)}` : 'Not fetched';
      return h('li', {},
        h('span', {}, mathText(doc.results[i].title),
          h('small', { class: errors.has(i) ? 'search-papers-error' : '', text: label })),
        h('button', { class: 'linkish', type: 'button', text: 'Remove', disabled: busy || preparing,
          'aria-label': `Remove ${doc.results[i].title} from selection`, onclick: () => {
            if (busy || preparing) return;
            selected.delete(i); message = ''; paint();
          } }));
    }));
    // The full companion render typesets detached content after mounting it.
    // Only typeset our own updates once mounted, avoiding a second KaTeX pass.
    if (here()) typeset(review);
  }

  async function fetchSelected() {
    if (busy || preparing || !here()) return;
    const queue = picked().filter(i => !papers.has(i));
    if (!queue.length) return;
    busy = true; cancelled = false;
    let completed = 0;
    for (const i of queue) {
      if (cancelled || !here()) break;
      current = i; errors.delete(i);
      message = `Fetching ${completed + 1} of ${queue.length}: ${doc.results[i].title}`;
      paint();
      try {
        // Do not abort the HTTP request: the server may still finish importing
        // that PDF. Finish this one, report it, and stop before the next.
        const paper = await api('POST', `/api/papers/${slug}/fetch`,
          { run: doc.run_id, index: i, reuse: true });
        papers.set(i, paper); errors.delete(i);
      } catch (err) { errors.set(i, err.message); }
      completed++;
    }
    busy = false; current = null;
    message = cancelled ? 'Downloads stopped. Review the papers below; those already added remain in your library.' : '';
    if (here()) paint();
  }

  paint();
  return {
    panel,
    checkbox(i) {
      if (!available.includes(i)) return null;
      const box = h('input', { type: 'checkbox', 'aria-label': `Select ${doc.results[i].title}` });
      boxes.set(i, box);
      box.addEventListener('change', () => {
        if (!busy && !preparing) {
          if (!box.checked) selected.delete(i);
          else if (selected.size < max) selected.add(i);
          message = '';
        }
        paint();
      });
      paint();
      return h('label', { class: 'result-select' }, box, 'Select for companion');
    },
    remember(i, paper) {
      papers.set(i, paper); errors.delete(i);
      if (here()) paint();
    },
    dispose() { disposed = true; cancelled = true; },
  };
}
