/* Light or dark, as chosen in Advanced settings (Tancredi, 2026-10-23) — or,
 * by default, as this computer is set.
 *
 * A plain script, read before the stylesheet, so that the page is drawn in
 * the chosen colours from its first frame instead of flashing the other ones.
 * The choice is one of the settings this browser keeps (settings.js); another
 * tab that changes it is followed here. */

(() => {
  const root = document.documentElement;
  const apply = () => {
    let theme = null;
    try { theme = JSON.parse(localStorage.getItem('deslopify.settings') || 'null')?.theme; }
    catch { /* storage off: as this computer is set */ }
    if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
    else delete root.dataset.theme;
  };
  apply();
  window.addEventListener('storage', (e) => { if (e.key === 'deslopify.settings') apply(); });
})();
