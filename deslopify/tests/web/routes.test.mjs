// Run with: node --experimental-vm-modules --test deslopify/tests/web/routes.test.mjs
// Load the page with inert dependencies so its real URL helpers can be tested
// without opening a browser, starting a server, or calling an AI.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/app.js', import.meta.url), 'utf8');
const exposed = source.replace(/\nroute\(\);\s*$/, '\nexport { parseRoute, levelHash, sideHash };');
assert.notEqual(exposed, source, 'Replace only the initial navigation with test exports');
const imports = new Map(Array.from(
  source.matchAll(/^import \{([^}]+)\} from '([^']+)';$/gm),
  ([, names, path]) => [path, names.split(',').map(name => name.trim())],
));
const location = { hash: '' };
const context = vm.createContext({
  location,
  document: { addEventListener() {} },
  window: { addEventListener() {} },
  matchMedia: () => ({ matches: false }),
  ResizeObserver: class { observe() {} },
});
const page = new vm.SourceTextModule(exposed, { context });
await page.link(specifier => {
  const names = imports.get(specifier) || ['init', 'load'];
  return new vm.SyntheticModule(names, function () {
    for (const name of names) this.setExport(name, () => {});
  }, { context });
});
await page.evaluate();
const { parseRoute, levelHash, sideHash } = page.namespace;
const parse = hash => {
  location.hash = hash;
  return JSON.parse(JSON.stringify(parseRoute()));
};

for (const level of ['expert', 'general', 'collection-expert', 'collection-nonexpert', 'search']) {
  test(`${level} run links preserve the selected reading`, () => {
    const slug = '3-papers-scalar-mean-rigidity-for-hyperbolic-bal';
    for (const run of ['2026-10-26T10-22-17Z-7d72', '2026-10-26T13-53-59Z-9ba1']) {
      assert.deepEqual(parse(levelHash(slug, level, run)), { slug, view: 'main', level, run });
      assert.deepEqual(parse(sideHash(slug, level, run)), { slug, view: 'side', level, run });
    }
    assert.deepEqual(parse(levelHash(slug, level)), { slug, view: 'main', level, run: null });
  });
}

test('paper, run history, and getting-ready routes keep their meanings', () => {
  assert.deepEqual(parse('#/p/a-paper/paper/7'), { slug: 'a-paper', view: 'paper', page: 7 });
  assert.deepEqual(parse('#/p/a-paper/runs'), { slug: 'a-paper', view: 'runs' });
  assert.deepEqual(parse('#/p/a-paper'), { slug: 'a-paper', view: 'main', level: null, run: null });
  assert.deepEqual(parse('#/ready'), { view: 'ready' });
});
