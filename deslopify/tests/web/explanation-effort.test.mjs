import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/settings.js', import.meta.url), 'utf8');
const context = vm.createContext({
  window: { addEventListener() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
});
const settings = new vm.SourceTextModule(source, { context });
await settings.link(path => {
  const names = path === './dom.js' ? ['api', 'h', 'mount']
    : path === './ready.js' ? ['deepseekKey', 'openrouterKey'] : [];
  return new vm.SyntheticModule(names, function () {
    for (const name of names) this.setExport(name, () => {});
  }, { context });
});
await settings.evaluate();
const { get, set, setExplainerDefault, explanationEffort } = settings.namespace;
const groups = [
  { account: 'codex', efforts: ['low', 'medium', 'high', 'xhigh', 'max'].map(id => ({ id })) },
  { account: 'deepseek', efforts: ['low', 'medium', 'high', 'max'].map(id => ({ id })) },
];

test('answer effort defaults to high and is independent from the writer', () => {
  assert.equal(get().explainerEffort, 'high');
  set({ reading: { effort: 'low' } });
  setExplainerDefault({ account: 'deepseek' });
  assert.equal(explanationEffort(groups), 'high');
});

test('following another companion model uses an effort its provider supports', () => {
  set({ explainerEffort: 'xhigh' });
  setExplainerDefault({ account: 'codex' });
  assert.equal(explanationEffort(groups), 'xhigh');
  setExplainerDefault({ account: 'deepseek' });
  assert.equal(explanationEffort(groups), 'high');
  assert.equal(explanationEffort(groups, { account: 'codex' }), 'xhigh');
  set({ explainerEffort: 'max' });
  assert.equal(explanationEffort(groups), 'max');
  setExplainerDefault(null);
  assert.equal(explanationEffort(groups), 'high');
});
