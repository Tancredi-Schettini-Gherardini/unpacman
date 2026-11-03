// Real selection controller, deferred network requests, no paid AI calls.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/search-papers.js', import.meta.url), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag, attrs = {}, ...children) {
    this.tag = tag; this.attrs = attrs; this.listeners = {};
    this.disabled = !!attrs.disabled; this.hidden = !!attrs.hidden;
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) this.addEventListener(key.slice(2), value);
    }
    this.replaceChildren(attrs.text || '', ...children);
  }
  replaceChildren(...children) {
    for (const child of this.children || []) if (child instanceof Element) child.parent = null;
    this.children = children.flat(Infinity).filter(c => c != null && c !== false);
    for (const child of this.children) if (child instanceof Element) child.parent = this;
  }
  get textContent() { return this.children.map(c => c.textContent ?? c).join(''); }
  set textContent(text) { this.replaceChildren(text); }
  get isConnected() { return this.root || !!this.parent?.isConnected; }
  querySelectorAll(tag) {
    return this.children.flatMap(c => c instanceof Element
      ? [...(c.tag === tag ? [c] : []), ...c.querySelectorAll(tag)] : []);
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name) { return Promise.all((this.listeners[name] || []).map(fn => fn({ target: this }))); }
  click() { return this.disabled || this.hidden ? Promise.resolve() : this.dispatch('click'); }
}

async function setup({ count = 3, draft = {}, onContinue = null, noPdf = [] } = {}) {
  const requests = [], launches = [];
  const context = vm.createContext({});
  const helpers = {
    api: (method, url, body) => {
      const request = { ...deferred(), method, url, body };
      requests.push(request); return request.promise;
    },
    h: (tag, attrs, ...children) => new Element(tag, attrs, ...children),
    mount: (el, ...children) => el.replaceChildren(...children),
    mathText: t => t, typeset() {},
  };
  const dom = new vm.SyntheticModule(Object.keys(helpers), function () {
    for (const [key, value] of Object.entries(helpers)) this.setExport(key, value);
  }, { context });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => dom); await module.evaluate();
  const picker = module.namespace.createSearchPapers({
    slug: 'search', doc: { run_id: 'S1', results: Array.from({ length: count }, (_, i) => ({
      title: `Paper ${i}`, pdf: noPdf.includes(i) ? '' : `https://example.org/${i}.pdf`,
    })) }, draft,
    onContinue: async (papers, here) => {
      launches.push(papers); if (onContinue) await onContinue(papers, here);
    },
  });
  const root = new Element('main', {}, picker.panel);
  root.root = true;
  const labels = Array.from({ length: count }, (_, i) => picker.checkbox(i));
  root.replaceChildren(picker.panel, ...labels);
  const inputs = labels.map(label => label?.querySelectorAll('input')[0]);
  const button = text => root.querySelectorAll('button').find(b => b.textContent === text);
  const select = async i => { inputs[i].checked = true; await inputs[i].dispatch('change'); };
  return { root, picker, draft, inputs, button, select, requests, launches };
}

test('select, fetch sequentially, review, then explicitly continue; no run is started', async () => {
  const ui = await setup();
  assert.equal(ui.button('Fetch selected papers').disabled, true);
  await ui.button('Select all').click();
  assert.equal(ui.requests.length, 0);
  const fetching = ui.button('Fetch selected papers').click();
  assert.equal(ui.requests.length, 1);
  assert.equal(JSON.stringify(ui.requests[0].body), JSON.stringify({ run: 'S1', index: 0, reuse: true }));
  assert.ok(ui.inputs.every(i => i.disabled));
  ui.requests[0].resolve({ slug: 'A', title: 'A', existing: true }); await flush();
  assert.equal(ui.requests.length, 2);
  ui.requests[1].resolve({ slug: 'B', title: 'B' }); await flush();
  ui.requests[2].resolve({ slug: 'C', title: 'C' }); await fetching;
  assert.equal(ui.launches.length, 0);
  assert.match(ui.root.textContent, /Already in your library/);
  await ui.button('Continue with 3 papers').click();
  assert.deepEqual(Array.from(ui.launches[0], p => p.slug), ['A', 'B', 'C']);
  assert.ok(ui.requests.every(r => r.url === '/api/papers/search/fetch' && r.method === 'POST'));
});

test('failure is visible, other downloads proceed, retry fetches only the missing paper', async () => {
  const ui = await setup({ count: 2 });
  await ui.button('Select all').click();
  const fetching = ui.button('Fetch selected papers').click();
  ui.requests[0].reject(new Error('No public PDF')); await flush();
  ui.requests[1].resolve({ slug: 'B' }); await fetching;
  assert.match(ui.root.textContent, /Could not fetch: No public PDF/);
  await ui.button('Continue with 1 paper').click();
  assert.deepEqual(Array.from(ui.launches[0], p => p.slug), ['B']);
  const retry = ui.button('Retry / fetch remaining papers').click();
  assert.equal(ui.requests[2].body.index, 0);
  ui.requests[2].resolve({ slug: 'A' }); await retry;
  assert.equal(ui.requests.length, 3);
  assert.ok(ui.button('Continue with 2 papers'));
  assert.doesNotMatch(ui.root.textContent, /No public PDF/);
});

test('cancel finishes only the current import, retains it, and allows an explicit resume', async () => {
  const ui = await setup();
  await ui.button('Select all').click();
  const fetching = ui.button('Fetch selected papers').click();
  await ui.button('Cancel downloads').click();
  assert.match(ui.root.textContent, /Stopping after the current download/);
  ui.requests[0].resolve({ slug: 'A' }); await fetching;
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.launches.length, 0);
  assert.equal(ui.button('Continue with 1 paper').disabled, false);
  assert.match(ui.root.textContent, /Downloads stopped/);
  const resume = ui.button('Fetch selected papers').click();
  assert.equal(ui.requests[1].body.index, 1);
  ui.requests[1].resolve({ slug: 'B' }); await flush();
  ui.requests[2].resolve({ slug: 'C' }); await resume;
  assert.equal(ui.requests.length, 3);
});

test('leaving the search discards late navigation and stops the download queue', async () => {
  for (const disconnect of [false, true]) {
    const ui = await setup();
    await ui.button('Select all').click();
    const fetching = ui.button('Fetch selected papers').click();
    if (disconnect) ui.root.root = false;
    else ui.picker.dispose();
    ui.requests[0].resolve({ slug: 'A' }); await fetching;
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.launches.length, 0);
    // A completed import is still known when returning to this search.
    const returned = await setup({ draft: ui.draft });
    assert.ok(returned.inputs.every(i => i.checked));
    assert.ok(returned.button('Continue with 1 paper'));
  }
});

test('the limit is 12 and papers without PDFs cannot be selected', async () => {
  const ui = await setup({ count: 15, noPdf: [0, 2] });
  assert.equal(ui.inputs[0], undefined);
  await ui.button('Select first 12 available').click();
  assert.equal(ui.inputs.filter(i => i?.checked).length, 12);
  assert.equal(ui.inputs[14].disabled, true);
  await ui.button('Remove').click();
  assert.equal(ui.inputs[14].disabled, false);
  await ui.select(14);
  assert.equal(ui.inputs.filter(i => i?.checked).length, 12);
});

test('review deduplicates identical imported PDFs and Remove affects only the selection', async () => {
  const ui = await setup({ count: 2 });
  await ui.button('Select all').click();
  ui.picker.remember(0, { slug: 'same', existing: true });
  ui.picker.remember(1, { slug: 'same', existing: true });
  await ui.button('Continue with 1 paper').click();
  assert.equal(ui.launches[0].length, 1);
  await ui.button('Remove').click();
  assert.equal(ui.inputs[0].checked, false);
  assert.equal(ui.inputs[1].checked, true);
  await ui.button('Clear selection').click();
  assert.equal(ui.button('Continue with 0 papers').hidden, true);
  assert.equal(ui.requests.length, 0); // no DELETE, fetch, or run
  await ui.select(0);
  assert.ok(ui.button('Continue with 1 paper'));
});

test('failed collection preparation keeps imported papers for retry and ignores double clicks', async () => {
  const prepare = deferred();
  const ui = await setup({ count: 2, onContinue: () => prepare.promise });
  await ui.button('Select all').click();
  ui.picker.remember(0, { slug: 'A' }); ui.picker.remember(1, { slug: 'B' });
  const next = ui.button('Continue with 2 papers');
  const continuing = next.click();
  await next.dispatch('click');
  assert.equal(ui.launches.length, 1);
  prepare.reject(new Error('Collection failed')); await continuing;
  assert.match(ui.root.textContent, /Could not open run settings: Collection failed/);
  assert.equal(next.disabled, false);
  assert.equal(ui.requests.length, 0);
});

test('the launch callback can detect that the user left during collection creation', async () => {
  const prepare = deferred();
  let navigated = false;
  const ui = await setup({ onContinue: async (papers, here) => {
    await prepare.promise; if (here()) navigated = true;
  } });
  await ui.select(0); ui.picker.remember(0, { slug: 'A' });
  const continuing = ui.button('Continue with 1 paper').click();
  ui.picker.dispose(); prepare.resolve(); await continuing;
  assert.equal(navigated, false);
});
