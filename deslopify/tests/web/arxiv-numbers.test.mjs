// Run with: node --experimental-vm-modules --test deslopify/tests/web/arxiv-numbers.test.mjs
// The arXiv numbers the page finds in what is typed or pasted (app.js
// arxivItems, 2026-10-28): the page's real code, loaded with inert
// dependencies as routes.test.mjs loads it — no browser, server or arXiv.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/app.js', import.meta.url), 'utf8');
const exposed = source.replace(/\nroute\(\);\s*$/, '\nexport { arxivItems };');
assert.notEqual(exposed, source, 'Replace only the initial navigation with test exports');
const imports = new Map(Array.from(
  source.matchAll(/^import \{([^}]+)\} from '([^']+)';$/gm),
  ([, names, path]) => [path, names.split(',').map(name => name.trim())],
));
const context = vm.createContext({
  location: { hash: '' },
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
const { arxivItems } = page.namespace;
// Made in the page's own realm: brought into this one to be compared.
const numbers = text => JSON.parse(JSON.stringify(arxivItems(text).map(item => item.arxiv)));

test('a number is found alone, as a paper prints it, and in an address', () => {
  assert.deepEqual(numbers('2309.01234'), ['2309.01234']);
  assert.deepEqual(numbers('  arXiv:2309.01234v2  '), ['2309.01234v2']);
  assert.deepEqual(numbers('arXiv:math/0211159v1  [math.DG]  11 Nov 2002'), ['math/0211159v1']);
  assert.deepEqual(numbers('https://arxiv.org/abs/math.GT/0309136?context=math'), ['math.GT/0309136']);
  assert.deepEqual(numbers('https://arxiv.org/pdf/2309.01234v3.pdf'), ['2309.01234v3']);
  assert.deepEqual(numbers('10.48550/arXiv.2309.01234'), ['2309.01234']);
  assert.deepEqual(numbers('HEP-TH/9901001'), ['HEP-TH/9901001']);
});

test('several numbers are several papers, each once', () => {
  assert.deepEqual(numbers('math/0211159, math/0309136; 2001.00001v2'),
                   ['math/0211159', 'math/0309136', '2001.00001v2']);
  assert.deepEqual(numbers('hep-th/9901001 and cond-mat.str-el/0012345'),
                   ['hep-th/9901001', 'cond-mat.str-el/0012345']);
  assert.deepEqual(numbers('2309.01234\n2309.01234'), ['2309.01234']);
});

test('text with no number is sent as it is, for the server to say so; nothing is nothing', () => {
  assert.deepEqual(numbers('Perelman entropy'), ['Perelman entropy']);
  assert.deepEqual(numbers('12309.01234'), ['12309.01234']);
  assert.deepEqual(numbers('2309.012345'), ['2309.012345']);
  assert.deepEqual(numbers(''), []);
  assert.deepEqual(numbers('   '), []);
});

test('each goes where a chosen PDF goes, named for the lists that show it', () => {
  assert.deepEqual(JSON.parse(JSON.stringify(arxivItems('2309.01234'))),
                   [{ name: 'arXiv:2309.01234', type: 'application/pdf', arxiv: '2309.01234' }]);
});
