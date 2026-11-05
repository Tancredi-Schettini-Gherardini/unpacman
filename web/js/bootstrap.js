// The address `atelier serve` prints and opens carries a key after `#auth=`,
// which a browser never sends to a server (src/atelier/web/security.py). Keep
// it in this browser, for this address only; trade it once for the cookie that
// lets the page read its event stream and show files in frames; then start.
const KEY = 'atelier.controlToken';
const fromLaunch = new URLSearchParams(location.hash.slice(1)).get('auth');
if (fromLaunch) {
  try { localStorage.setItem(KEY, fromLaunch); } catch { /* storage off */ }
  history.replaceState(history.state, '', location.pathname + location.search);
}
let saved = null;
try { saved = localStorage.getItem(KEY); } catch { /* storage off */ }
const token = fromLaunch || saved;

function locked() {
  const content = document.querySelector('#content');
  const message = document.createElement('div');
  message.className = 'empty';
  message.textContent = 'Open Atelier from the address `atelier serve` printed in Terminal. '
    + 'It ends in #auth=… and changes each time Atelier starts.';
  content?.replaceChildren(message);
}

let opened = false;
try {
  const response = await fetch('/api/session', {
    method: 'POST', headers: token ? { 'X-Atelier-Token': token } : {},
  });
  opened = response.ok;
} catch { /* the lab is not running */ }
if (opened) import('./app.js');
else locked();
