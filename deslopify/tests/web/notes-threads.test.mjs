// Run with: node --experimental-vm-modules --test deslopify/tests/web/notes-threads.test.mjs
// The explanations' own order (Tancredi, 2026-11-11): the notes of the run on
// screen only — a note asked before the paper had any reading with every run —
// each followed by its thread, the notes about words of its answers; and the
// same words asked again in the same place open the note already there. The
// real notes.js, loaded with inert dependencies: no browser, server or AI.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/notes.js', import.meta.url), 'utf8');
const exposed = `${source}\nexport { arrange, belongs, earlier, threadParent, S };\n`;
const imports = new Map(Array.from(
  source.matchAll(/^import \{([^}]+)\} from '([^']+)';$/gm),
  ([, names, path]) => [path, names.split(',').map(name => name.trim())],
));
const context = vm.createContext({ window: {} });
const notes = new vm.SourceTextModule(exposed, { context });
await notes.link(specifier => {
  const names = imports.get(specifier) || ['init', 'load'];
  return new vm.SyntheticModule(names, function () {
    for (const name of names) this.setExport(name, () => {});
  }, { context });
});
await notes.evaluate();
const { arrange, belongs, earlier, threadParent, S } = notes.namespace;

const note = (uid, extra = {}) => ({
  uid, key: `k-${uid}`, state: 'open', status: 'done',
  where: { kind: 'companion', section: 'main' }, reading: { level: 'expert', run: 'R1' },
  ...extra,
});
const under = (uid, parent, extra = {}) => note(uid, { where: { kind: 'note', note: parent }, ...extra });

function onScreen(cards, run = 'R1') {
  S.slug = 'a-paper';
  S.cards = cards;
  S.reading = run === 'no reading' ? null : { slug: 'a-paper', level: 'expert', run };
  S.hooks.reading = () => null;
}
// Values made inside the page's context, compared as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
const order = () => plain(arrange().map(o => o.card.uid));
const field = (name) => plain(Object.fromEntries(arrange().map(o => [o.card.uid, o[name]])));

test('each note is followed by its thread, oldest first at every depth', () => {
  onScreen([note('a'), under('b', 'a'), note('c'), under('d', 'b'), under('e', 'a')]);
  assert.deepEqual(order(), ['a', 'b', 'd', 'e', 'c']);
  assert.deepEqual(field('depth'), { a: 0, b: 1, d: 2, e: 1, c: 0 });
  assert.deepEqual(field('below'), { a: 3, b: 1, d: 0, e: 0, c: 0 });
  assert.deepEqual(field('hidden'), { a: false, b: false, d: false, e: false, c: false });
});

test('a folded note folds its whole thread, and only its own', () => {
  const a = note('a', { state: 'folded' });
  onScreen([a, under('b', 'a'), note('c'), under('d', 'b'), under('e', 'a')]);
  assert.deepEqual(field('hidden'), { a: false, b: true, d: true, e: true, c: false });
  a.state = 'open';
  S.cards[1].state = 'folded';                 // b, down the thread
  assert.deepEqual(field('hidden'), { a: false, b: false, d: true, e: false, c: false });
});

test('each run shows its own notes; one asked with no reading shows with every run', () => {
  const cards = [note('r1'), note('r2', { reading: { level: 'expert', run: 'R2' } }),
                 note('paper', { reading: { level: 'expert', run: null } }),
                 note('old', { reading: null })];
  onScreen(cards, 'R1');
  assert.deepEqual(order(), ['r1', 'paper', 'old']);
  onScreen(cards, 'R2');
  assert.deepEqual(order(), ['r2', 'paper', 'old']);
  onScreen(cards, null);                       // a reading with no run of its own
  assert.deepEqual(order(), ['paper', 'old']);
  onScreen(cards, 'no reading');
  assert.deepEqual(order(), ['paper', 'old']);
  assert.equal(belongs(cards[1]), false);
});

test('the reading on screen is the page\'s own when it has one for this paper', () => {
  const cards = [note('r1'), note('r2', { reading: { level: 'expert', run: 'R2' } })];
  onScreen(cards, 'R1');
  S.hooks.reading = () => ({ slug: 'a-paper', level: 'expert', run: 'R2' });
  assert.deepEqual(order(), ['r2']);
  S.hooks.reading = () => ({ slug: 'another-paper', level: 'expert', run: 'R2' });
  assert.deepEqual(order(), []);               // another paper's reading is not this one's
  S.hooks.reading = () => null;
});

test('a note whose note is not shown stands on its own, nothing lost', () => {
  onScreen([under('orphan', 'gone'),
            note('elsewhere', { reading: { level: 'expert', run: 'R2' } }),
            under('child', 'elsewhere'), under('grandchild', 'child')]);
  assert.deepEqual(order(), ['orphan', 'child', 'grandchild']);
  assert.deepEqual(field('depth'), { orphan: 0, child: 0, grandchild: 1 });
});

test('a loop in the links leaves its notes on their own, each drawn once', () => {
  onScreen([under('x', 'y'), under('y', 'x'), under('z', 'x'), note('w')]);
  assert.deepEqual(order(), ['x', 'y', 'z', 'w']);
  const shown = new Map(S.cards.map(c => [c.uid, c]));
  assert.equal(threadParent(S.cards[0], shown), null);
  assert.equal(threadParent(S.cards[2], shown), null);   // its note is in the loop
});

test('the same words in the same place open the note already there — and only there', () => {
  const top = note('top', { key: 'same' });
  const inA = under('inA', 'a', { key: 'same' });
  const failed = note('failed', { key: 'other', status: 'error' });
  const r2 = note('r2', { key: 'r2-key', reading: { level: 'expert', run: 'R2' } });
  onScreen([note('a'), note('b'), top, inA, failed, r2]);
  assert.equal(earlier('same', { kind: 'companion' }), top);
  assert.equal(earlier('same', { kind: 'paper', page: 3 }), top);   // the text read, either way
  assert.equal(earlier('same', { kind: 'note', note: 'a' }), inA);
  assert.equal(earlier('same', { kind: 'note', note: 'b' }), null);  // another note's thread
  assert.equal(earlier('other', { kind: 'companion' }), null);      // a failed one is asked again
  assert.equal(earlier('other', { kind: 'companion' }, c => c.status === 'error'), failed);
  assert.equal(earlier('r2-key', { kind: 'companion' }), null);     // another run's
  onScreen(S.cards, 'R2');
  assert.equal(earlier('r2-key', { kind: 'companion' }), r2);
});
