// Run with: node --experimental-vm-modules --test deslopify/tests/web/math.test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const katex = createRequire(import.meta.url)('../../../web/vendor/katex/katex.min.js');
const source = await readFile(new URL('../../deslopify/app/web/dom.js', import.meta.url), 'utf8');

test('the companion web renderer defines the same Sha symbol as PDF export', async () => {
  const rendered = [];
  const macros = [];
  const context = vm.createContext({ window: { katex: { render(tex, el, options) {
    macros.push(options.macros);
    rendered.push(katex.renderToString(tex, { ...options, throwOnError: true }));
  } } } });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => { throw new Error('No imports expected'); });
  await module.evaluate();
  const el = { textContent: String.raw`\Sha(E)`, tagName: 'SPAN',
    classList: { contains: () => false } };
  module.namespace.typeset({ querySelectorAll: () => [el, el] });
  assert.equal(rendered.length, 2);
  assert.match(rendered[0], /Ш/);
  assert.doesNotMatch(rendered[0], /katex-error/);
  assert.notEqual(macros[0], macros[1]);
  assert.throws(() => katex.renderToString(String.raw`\UnknownScientificMacro`, {
    macros: macros[0], throwOnError: true,
  }), /Undefined control sequence/);
});

test('numbered displays keep the formula and tag in separate layout cells', async () => {
  class Element {
    constructor(classes = []) {
      this.children = [];
      this.parent = null;
      this.classes = new Set(classes);
      this.classList = {
        contains: name => this.classes.has(name),
        add: name => this.classes.add(name),
      };
    }
    get firstChild() { return this.children[0] ?? null; }
    append(child) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1);
      this.children.push(child);
      child.parent = this;
    }
    insertBefore(child, sibling) {
      this.children.splice(this.children.indexOf(sibling), 0, child);
      child.parent = this;
    }
    querySelector(selector) {
      if (selector === ':scope > .tag') return this.children.find(c => c.classes.has('tag'));
      return null;
    }
  }
  const html = new Element();
  const first = new Element(['base']);
  const second = new Element(['base']);
  const tag = new Element(['tag']);
  html.append(first);
  html.append(second);
  html.append(tag);
  const display = {
    textContent: String.raw`x+y=z\tag{C.5}`,
    tagName: 'DIV',
    classList: { contains: () => true },
    querySelector: () => html,
  };
  const context = vm.createContext({
    document: { createElement: () => new Element() },
    window: { katex: { render() {} } },
  });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => { throw new Error('No imports expected'); });
  await module.evaluate();
  module.namespace.typeset({ querySelectorAll: () => [display] });
  assert.ok(html.classes.has('numbered-equation'));
  assert.equal(html.children.length, 2);
  assert.deepEqual(html.children[0].children, [first, second]);
  assert.equal(html.children[1], tag);
});
