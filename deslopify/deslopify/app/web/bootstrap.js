// The launch URL carries its secret after #, which browsers never send to the
// server. Exchange it once, then keep it only in this origin's local storage.
const key = 'unpacman.controlToken';
const fragment = new URLSearchParams(location.hash.slice(1));
const fromLaunch = fragment.get('auth');
if (fromLaunch) {
  try { localStorage.setItem(key, fromLaunch); } catch { /* private browsing */ }
  history.replaceState(null, '', location.pathname + location.search);
}
let saved = null;
try { saved = localStorage.getItem(key); } catch { /* private browsing */ }
const token = fromLaunch || saved;

function locked() {
  const view = document.querySelector('#view');
  const message = document.createElement('p');
  message.className = 'lede';
  // On Windows (WSL) the address is printed in unpacman's own window (wsl.py).
  message.textContent = document.documentElement.dataset.platform === 'wsl'
    ? 'Open unpacman from the Start menu, or use the full address printed in its own window.'
    : 'Open unpacman from its icon, or use the full URL printed when you start it in Terminal.';
  view.replaceChildren(message);
}

async function oldServerStillRunning() {
  // An already-running pre-upgrade server serves the new static files before
  // its Python process is restarted. Let an active reading finish undisturbed.
  try {
    const response = await fetch('/api/hello', { cache: 'no-store' });
    if (response.ok && (await response.json()).app === 'unpacman') {
      const notice = document.createElement('div');
      notice.className = 'toast';
      notice.textContent = 'Restart unpacman after its current readings finish to activate the security update.';
      document.querySelector('#toasts').append(notice);
      import('./app.js');
      return true;
    }
  } catch { /* no old server */ }
  return false;
}

if (token) {
  let authenticated = false;
  try {
    const response = await fetch('/api/session', {
      method: 'POST', headers: { 'X-Unpacman-Token': token },
    });
    authenticated = response.ok;
  } catch { /* no server or stale link */ }
  if (authenticated) {
    window.unpacmanToken = token;
    import('./app.js');
  } else if (!(await oldServerStillRunning())) {
    try { localStorage.removeItem(key); } catch { /* private browsing */ }
    locked();
  }
} else if (!(await oldServerStillRunning())) {
  locked();
}
