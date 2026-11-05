/* What the page itself noticed going wrong, kept for the session, so that a
 * report sent with Send feedback can say it (feedback.js): a paper that could
 * not be drawn, an error the page did not expect. A short line each — never
 * the paper's text, its title, or anyone's home folder. */

const KEY = 'unpacman.diag';
const MAX = 20;

function load() {
  try { return JSON.parse(sessionStorage.getItem(KEY) || '[]') || []; }
  catch { return []; }
}

const tidy = (text) => String(text || '')
  .replace(/\/Users\/[^/\s]+/g, '~')
  .replace(/\/api\/papers\/[^/\s?#]+/g, '/api/papers/…')
  .replace(/#\/p\/[^/\s]+/g, '#/p/…')
  .slice(0, 300);

export function note(kind, message) {
  const list = load();
  list.push({ at: new Date().toISOString(), kind, message: tidy(message) });
  try { sessionStorage.setItem(KEY, JSON.stringify(list.slice(-MAX))); }
  catch { /* private browsing: nothing kept */ }
}

// For the report: a heading and one line per event, or '' if nothing happened.
export function lines() {
  const list = load();
  if (!list.length) return '';
  return ['What the page noticed (this session):',
          ...list.map(e => `  ${String(e.at).slice(11, 19)} ${e.kind}: ${e.message}`)].join('\n');
}

window.addEventListener('error', (e) => {
  const file = String(e.filename || '').split('/').pop();
  note('error', `${e.message}${file ? ` (${file}:${e.lineno || '?'})` : ''}`);
});
window.addEventListener('unhandledrejection', (e) => {
  const reason = e.reason;
  // A request given up on purpose is not something that went wrong.
  if (reason?.name === 'AbortError') return;
  note('error', String(reason?.message || reason));
});
