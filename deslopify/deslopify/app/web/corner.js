/* The bottom-right corner, where things wait for the reader: the first run's
 * suggestion (firstask.js), the folded explanations and the button to ask a
 * question of one's own (notes.js) — stacked, never one on top of another.
 *
 * How tall it is, with the gap above it, is kept in --corner-h: the
 * explanations stop above it, and the end of the page can be scrolled clear
 * of it (app.css). */

let el = null;

export function corner() {
  if (!el) {
    el = document.createElement('div');
    el.className = 'corner';
    document.body.append(el);
    new ResizeObserver(() => {
      const tall = el.offsetHeight;
      document.documentElement.style.setProperty('--corner-h', `${tall ? tall + 10 : 0}px`);
      document.body.classList.toggle('cornered', tall > 0);
    }).observe(el);
  }
  return el;
}
