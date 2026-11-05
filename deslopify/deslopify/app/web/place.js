/* Which computer this is, as the server says on <html data-platform>
 * (server.index): "mac", "wsl" — Linux inside Windows — or "linux". The page
 * says "this Mac" only on a Mac (2026-10-17, branch unpacman_windows). */

const where = document.documentElement.dataset.platform || 'mac';

export const onMac = where === 'mac';
export const onWindows = where === 'wsl';
// A Linux computer, not Linux inside Windows (linux.py).
export const onLinux = where === 'linux';
export const here = onMac ? 'this Mac' : 'this computer';
// The keys that copy what is selected.
export const copyKey = onMac ? '⌘C' : 'Ctrl+C';
