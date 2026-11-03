// Run with: node --experimental-vm-modules --test deslopify/tests/web/questions.test.mjs
// Exercise the real questionnaire module with deferred requests and a small
// DOM double. No browser, server, or paid AI requests are needed.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../../deslopify/app/web/questions.js', import.meta.url), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag, attrs = {}, ...children) {
    this.tag = tag;
    this.attrs = attrs;
    this.disabled = !!attrs.disabled;
    this.dataset = {};
    this.listeners = {};
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) this.addEventListener(key.slice(2), value);
    }
    this.replaceChildren(attrs.text || '', ...children);
  }
  replaceChildren(...children) {
    for (const child of this.children || []) {
      if (child instanceof Element) {
        if (Element.focused && child.contains(Element.focused)) {
          Element.focused.blurs = (Element.focused.blurs || 0) + 1;
          Element.focused = null;
        }
        child.parent = null;
      }
    }
    this.children = children.flat(Infinity).filter(child => child != null && child !== false);
    for (const child of this.children) {
      if (child instanceof Element) {
        if (child.parent) child.parent.children = child.parent.children.filter(c => c !== child);
        child.parent = this;
      }
    }
  }
  get textContent() { return this.children.map(child => child.textContent ?? child).join(''); }
  set textContent(value) { this.replaceChildren(value); }
  get isConnected() { return this.root || !!this.parent?.isConnected; }
  contains(el) { return this === el || this.children.some(c => c instanceof Element && c.contains(el)); }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(s => s.trim());
    const matches = el => selectors.some(s => s.startsWith('.')
      ? (el.attrs.class || '').split(' ').includes(s.slice(1))
      : s === 'input:checked' ? el.tag === 'input' && el.checked : el.tag === s);
    return this.children.flatMap(child => child instanceof Element
      ? [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)] : []);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name) { return Promise.all((this.listeners[name] || []).map(fn => fn({ target: this }))); }
  click() { return this.disabled ? Promise.resolve() : this.dispatch('click'); }
  focus() {
    if (Element.focused && Element.focused !== this) {
      Element.focused.blurs = (Element.focused.blurs || 0) + 1;
    }
    Element.focused = this;
  }
}

const made = () => ({
  key: 'questions-v1', questions: [{ id: 'q1', kind: 'definition',
    question: 'Do you know what a metric is?', parent: null }],
});

async function setup() {
  const requests = [], starts = [], errors = [], timers = new Set();
  const run = deferred();
  const box = new Element('div');
  box.root = true;
  const mount = (el, ...children) => { el.replaceChildren(...children); return el; };
  const context = vm.createContext({
    AbortController, Date,
    sessionStorage: { getItem: () => null, setItem() {} },
    setInterval: fn => { timers.add(fn); return fn; },
    clearInterval: id => timers.delete(id),
  });
  const helpers = {
    api: (method, url, body, options = {}) => {
      const request = { ...deferred(), method, url, body, signal: options.signal };
      requests.push(request);
      // Deliberately allow settlement after abort: queued/late responses must
      // be harmless even when cancellation arrives too late to stop them.
      return request.promise;
    },
    h: (tag, attrs, ...children) => new Element(tag, attrs, ...children),
    mount, mathText: text => text, typeset() {},
    toast: message => errors.push(message),
  };
  const dom = new vm.SyntheticModule(Object.keys(helpers), function () {
    for (const [key, value] of Object.entries(helpers)) this.setExport(key, value);
  }, { context });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => dom);
  await module.evaluate();
  const pending = module.namespace.askFirst(box, {
    paper: { slug: 'test-paper', title: 'Test paper' }, pick: { model: 'test-model' },
    start: profile => { starts.push(profile); return run.promise; },
    back: () => mount(box, 'Choose a level'),
  });
  const button = text => box.querySelectorAll('button').find(b => b.textContent === text);
  return { box, requests, starts, errors, timers, run, pending, button, mount };
}

test('skip starts immediately, once, and late questions cannot replace the starting screen', async () => {
  const ui = await setup();
  const skip = ui.button('Skip the questions and start');
  const click = skip.click();
  assert.deepEqual(ui.starts, [null]);
  assert.equal(ui.requests[0].signal?.aborted, true);
  assert.equal(ui.timers.size, 0);
  await skip.dispatch('click'); // A second queued event must not start another run.
  ui.requests[0].resolve(made());
  await ui.pending;
  assert.equal(ui.box.querySelector('.qs-list'), null);
  assert.equal(ui.starts.length, 1);
  assert.match(skip.textContent, /Starting/);
  ui.run.resolve();
  await click;
});

test('late question errors cannot replace the start screen or show a retry', async () => {
  const ui = await setup();
  const click = ui.button('Skip the questions and start').click();
  ui.requests[0].reject(new Error('Questions timed out'));
  await ui.pending;
  assert.equal(ui.box.querySelector('.notice'), null);
  assert.equal(ui.button('Try again'), undefined);
  assert.deepEqual(ui.errors, []);
  ui.run.resolve();
  await click;
});

test('late questions do not overwrite a successfully started run in the same container', async () => {
  const ui = await setup();
  const click = ui.button('Skip the questions and start').click();
  ui.run.resolve();
  await click;
  ui.mount(ui.box, 'Reading started');
  ui.requests[0].resolve(made());
  await ui.pending;
  assert.equal(ui.box.textContent, 'Reading started');
});

test('going back discards late questions even when the container is reused', async () => {
  const ui = await setup();
  await ui.button('Back').click();
  ui.requests[0].resolve(made());
  await ui.pending;
  assert.equal(ui.box.textContent, 'Choose a level');
  assert.equal(ui.requests[0].signal?.aborted, true);
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.starts.length, 0);
});

test('a rejected start after skipping restores usable buttons without a stuck loading message', async () => {
  const ui = await setup();
  const click = ui.button('Skip the questions and start').click();
  ui.run.reject(new Error('Account is busy'));
  await click;
  assert.equal(ui.button('Skip the questions and start').disabled, false);
  assert.equal(ui.button('Back').disabled, false);
  assert.ok(ui.button('Try again'));
  assert.match(ui.box.textContent, /Account is busy/);
  assert.equal(ui.box.querySelector('.qs-wait'), null);
  ui.requests[0].resolve(made());
  await ui.pending;
  assert.equal(ui.box.querySelector('.qs-list'), null);
});

test('questions can be retried after a failed early start', async () => {
  const ui = await setup();
  const click = ui.button('Skip the questions and start').click();
  ui.run.reject(new Error('Account is busy'));
  await click;
  const retry = ui.button('Try again').click();
  assert.equal(ui.requests.length, 2);
  ui.requests[0].resolve(made());
  await ui.pending;
  assert.ok(ui.box.querySelector('.qs-wait'));
  ui.requests[1].resolve(made());
  await retry;
  assert.ok(ui.box.querySelector('.qs-list'));
});

test('normal answers still reach the run and remain available after a start error', async () => {
  const ui = await setup();
  ui.requests[0].resolve(made());
  await ui.pending;
  const yes = ui.box.querySelectorAll('input').find(el => el.attrs.value === 'yes');
  yes.checked = true;
  await yes.dispatch('change');
  await ui.button('Start the reading').click();
  assert.equal(JSON.stringify(ui.starts), JSON.stringify([{ key: 'questions-v1', answers: { q1: 'yes' } }]));
  ui.run.reject(new Error('Try later'));
  await flush();
  assert.equal(ui.button('Start the reading').disabled, false);
  assert.ok(ui.box.querySelector('.qs-list'));
  assert.deepEqual(ui.errors, ['Try later']);
});

test('skipping already loaded questions still starts without a reader profile', async () => {
  const ui = await setup();
  ui.requests[0].resolve(made());
  await ui.pending;
  const click = ui.button('Skip the questions and start').click();
  assert.deepEqual(ui.starts, [null]);
  ui.run.resolve();
  await click;
});

test('answering keeps focus without a blur while nested follow-ups appear and disappear', async () => {
  const ui = await setup();
  ui.requests[0].resolve({ key: 'questions-v1', questions: [
    { id: 'q1', question: 'Do you know what a metric is?' },
    { id: 'q2', question: 'Do you know what a manifold is?' },
    { id: 'q3', parent: 'q2', ask_if: ['no', 'roughly'], question: 'A topological space?' },
    { id: 'q4', parent: 'q3', ask_if: ['no'], question: 'An open set?' },
    { id: 'q5', question: 'Do you know what compactness means?' },
  ] });
  await ui.pending;
  const row = id => ui.box.querySelectorAll('li').find(el => el.attrs['data-id'] === id);
  const visible = id => !!row(id) && !row(id).hidden;
  const answer = async (id, value) => {
    const choices = row(id).querySelectorAll('input');
    const input = choices.find(el => el.attrs.value === value);
    input.focus();
    const blurs = input.blurs || 0;
    choices.forEach(el => { el.checked = el === input; });
    await input.dispatch('change');
    assert.equal(Element.focused, input, 'the selected answer keeps keyboard focus');
    assert.equal(input.blurs || 0, blurs, 'the selected answer must not lose focus during an update');
  };
  assert.equal(visible('q3'), false);
  assert.equal(visible('q4'), false);
  await answer('q5', 'yes'); // An ordinary answer without any follow-up.
  await answer('q2', 'no');
  assert.equal(visible('q3'), true);
  assert.equal(visible('q4'), false);
  await answer('q3', 'no');
  assert.equal(visible('q4'), true);
  await answer('q4', 'roughly');
  await answer('q2', 'yes');
  assert.equal(visible('q3'), false);
  assert.equal(visible('q4'), false);
  await answer('q2', 'roughly');
  assert.equal(visible('q4'), true);
  assert.equal(row('q4').querySelector('input:checked').attrs.value, 'roughly');
  await answer('q2', 'yes');
  assert.equal(ui.box.querySelector('.qs-count').textContent, '2 of 3 answered');
  const click = ui.box.querySelector('.start').click();
  assert.equal(JSON.stringify(ui.starts), JSON.stringify([
    { key: 'questions-v1', answers: { q5: 'yes', q2: 'yes' } },
  ]));
  ui.run.resolve();
  await click;
});

test('an answer shows or hides a follow-up in place, never rebuilding the list', async () => {
  // Rebuilding it took the answer just clicked off the page and put it back:
  // the browser scrolled to the first question, and every follow-up replayed
  // its entrance (2026-10-13).
  const ui = await setup();
  ui.requests[0].resolve({ key: 'questions-v1', questions: [
    { id: 'q1', kind: 'definition', question: 'Do you know what a metric is?', parent: null },
    { id: 'q2', kind: 'definition', question: 'And a manifold?', parent: 'q1', ask_if: ['no'] },
    { id: 'q3', kind: 'theorem', question: 'Do you know Stokes’ theorem?', parent: null }] });
  await ui.pending;
  const list = ui.box.querySelector('.qs-list');
  const items = [...list.children];
  assert.deepEqual(items.map(el => el.attrs['data-id']), ['q1', 'q2', 'q3']);
  assert.deepEqual(items.map(el => !!el.hidden), [false, true, false]);
  const answer = (id, value) => {
    const input = ui.box.querySelectorAll('input')
      .find(el => el.attrs.name === `q-${id}` && el.attrs.value === value);
    input.checked = true;
    return input.dispatch('change');
  };
  await answer('q1', 'no');
  assert.deepEqual([...list.children], items);
  assert.deepEqual(items.map(el => !!el.hidden), [false, false, false]);
  await answer('q2', 'yes');
  await answer('q1', 'yes');
  assert.deepEqual([...list.children], items);
  assert.equal(items[1].hidden, true);
  await answer('q3', 'roughly');
  // What is sent is what is on show: the hidden follow-up's answer stays out.
  await ui.button('Start the reading').click();
  assert.equal(JSON.stringify(ui.starts),
               JSON.stringify([{ key: 'questions-v1', answers: { q1: 'yes', q3: 'roughly' } }]));
  ui.run.resolve();
});
