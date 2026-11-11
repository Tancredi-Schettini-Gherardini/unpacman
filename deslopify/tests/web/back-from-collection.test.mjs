// Run with: node --experimental-vm-modules --test deslopify/tests/web/back-from-collection.test.mjs
// Back from a paper opened from the list of the papers read together with it
// goes back to them — their waiting page while they are read — not to a
// reading of that paper alone (Tancredi, 2026-11-11); and the reading a
// paper's PDF and runs stand for when none was on screen before them. The
// real app.js, loaded with inert dependencies: no browser, server or AI.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/app.js', import.meta.url), 'utf8');
const exposed = source.replace(/\nroute\(\);\s*$/,
  '\nexport { viewSwitch, paperReading, setFrom, storedFrom, state };');
assert.notEqual(exposed, source, 'Replace only the initial navigation with test exports');
const imports = new Map(Array.from(
  source.matchAll(/^import \{([^}]+)\} from '([^']+)';$/gm),
  ([, names, path]) => [path, names.split(',').map(name => name.trim())],
));
// Elements as plain records, so that a tab's address can be read back.
const element = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat(Infinity) });
const stored = new Map();
const sessionStorage = {
  getItem: key => (stored.has(key) ? stored.get(key) : null),
  setItem: (key, value) => stored.set(key, String(value)),
  removeItem: key => stored.delete(key),
};
const context = vm.createContext({
  location: { hash: '' },
  document: { addEventListener() {} },
  window: { addEventListener() {} },
  matchMedia: () => ({ matches: false }),
  ResizeObserver: class { observe() {} },
  sessionStorage,
});
const page = new vm.SourceTextModule(exposed, { context });
await page.link(specifier => {
  const names = imports.get(specifier) || ['init', 'load'];
  return new vm.SyntheticModule(names, function () {
    for (const name of names) this.setExport(name, name === 'h' ? element : () => {});
  }, { context });
});
await page.evaluate();
const { viewSwitch, paperReading, setFrom, storedFrom, state } = page.namespace;

const COLLECTION = '#/p/2-papers-a/collection-expert/2026-11-11T17-26-31Z-4379';
const tabs = (nav) => nav.kids.filter(Boolean).map(a => ({ view: a.attrs['data-view'], href: a.attrs.href }));
const first = (slug, label) => tabs(viewSwitch(slug, 'paper', label))[0];

test('a paper opened from its collection: Back returns to the collection', () => {
  state.reading = null;
  setFrom({ slug: 'paper-a', back: COLLECTION });
  assert.deepEqual(first('paper-a', 'Back'), { view: 'main', href: COLLECTION });
  // Kept for a reload of the tab.
  assert.deepEqual(JSON.parse(JSON.stringify(storedFrom())), { slug: 'paper-a', back: COLLECTION });
});

test('a paper with a reading of its own keeps its own first tab', () => {
  setFrom({ slug: 'paper-a', back: COLLECTION });
  assert.deepEqual(first('paper-a', 'Companion'), { view: 'main', href: '#/p/paper-a' });
  assert.deepEqual(first('paper-a', 'Reading'), { view: 'main', href: '#/p/paper-a' });
});

test('another paper, or none opened from a collection: Back as before', () => {
  setFrom({ slug: 'paper-a', back: COLLECTION });
  assert.deepEqual(first('paper-b', 'Back'), { view: 'main', href: '#/p/paper-b' });
  setFrom(null);
  assert.equal(storedFrom(), null);
  assert.deepEqual(first('paper-a', 'Back'), { view: 'main', href: '#/p/paper-a' });
});

test('only an address of a paper is taken back from the tab\'s storage', () => {
  sessionStorage.setItem('unpacman.fromCollection', JSON.stringify({ slug: 'paper-a', back: 'https://elsewhere.example' }));
  assert.equal(storedFrom(), null);
  sessionStorage.setItem('unpacman.fromCollection', '{not json');
  assert.equal(storedFrom(), null);
  sessionStorage.removeItem('unpacman.fromCollection');
});

test('a paper\'s PDF and runs stand for the reading its first tab opens', () => {
  const at = (status) => JSON.parse(JSON.stringify(paperReading('p', status)));
  // Being read: that reading, even with an earlier companion.
  assert.deepEqual(at({ level: 'expert', companion: true, companion_run: 'R1',
                        run: { id: 'R2', running: true } }), { slug: 'p', level: 'expert', run: 'R2' });
  // Read: its newest companion's run, even if a later reading failed.
  assert.deepEqual(at({ level: 'general', companion: true, companion_run: 'R1',
                        run: { id: 'R3', running: false } }), { slug: 'p', level: 'general', run: 'R1' });
  // A companion kept from before runs had names: no run.
  assert.deepEqual(at({ level: 'expert', companion: true, companion_run: null,
                        run: { id: 'R3', running: false } }), { slug: 'p', level: 'expert', run: null });
  // Only a failed reading: that reading.
  assert.deepEqual(at({ level: 'expert', companion: false, run: { id: 'R4', running: false } }),
                   { slug: 'p', level: 'expert', run: 'R4' });
  // Never read: no run.
  assert.deepEqual(at({ level: null, companion: false, run: null }), { slug: 'p', level: null, run: null });
});
