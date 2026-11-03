// Shell: routing, header, sidebar, keyboard, theme.

import { api, authHeaders, stream } from './api.js';
import { $, ago, clear, dot, h, mount, toast, usd } from './util.js';

export const state = {
  config: null,
  problems: [],
  active: [],            // live run snapshots
  accounts: [],
  route: { name: 'problems', slug: null, runId: null },
};

const views = {};        // name -> module (lazily imported)

// ------------------------------------------------------------------ routing

export function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState({}, '', path);
  else history.pushState({}, '', path);
  render();
}

function parseRoute() {
  const parts = location.pathname.split('/').filter(Boolean);
  if (!parts.length) return { name: 'problems', slug: null, runId: null };
  const [head, second] = parts;
  if (head === 'problems') return { name: 'problems', slug: second || null, runId: null };
  if (head === 'runs') return { name: 'runs', slug: null, runId: second || null };
  if (head === 'harnesses') return { name: 'harnesses', slug: second || null, runId: null };
  if (head === 'accounts') return { name: 'accounts', slug: null, runId: null };
  return { name: 'problems', slug: null, runId: null };
}

async function viewFor(name) {
  if (!views[name]) {
    views[name] = await import(`./views/${name}.js`);
  }
  return views[name];
}

let renderToken = 0;

export async function render() {
  const token = ++renderToken;
  state.route = parseRoute();
  const { name } = state.route;

  document.querySelectorAll('nav.tabs a').forEach(a =>
    a.classList.toggle('on', a.dataset.nav === name));

  stream.offAll('run:');           // views re-subscribe to what they need

  const content = $('#content');
  const view = await viewFor(name);
  if (token !== renderToken) return;

  try {
    await view.render(content, state.route);
  } catch (e) {
    console.error(e);
    mount(content, h('div', { class: 'empty err', text: e.message }));
  }

  renderSidebar();
}

// ------------------------------------------------------------------ sidebar

export function renderSidebar() {
  const side = $('#side');
  const { name, slug } = state.route;

  if (name === 'problems') {
    const filter = h('input', {
      type: 'search', placeholder: 'Filter problems…', id: 'pfilter',
      oninput: (e) => filterProblems(e.target.value),
    });
    mount(side,
      h('div', { class: 'side-head' }, filter),
      h('div', { class: 'side-list', id: 'plist' }),
      h('div', { class: 'side-foot' },
        h('button', {
          class: 'btn sm', style: { width: '100%' },
          onclick: newProblemDialog,
        }, '+ New problem')));
    paintProblems();
  } else {
    mount(side,
      h('div', { class: 'side-head' },
        h('div', { class: 'stat-k', text: 'Live sessions' })),
      h('div', { class: 'side-list', id: 'plist' }));
    paintActive();
  }
}

function filterProblems(q) {
  const needle = q.trim().toLowerCase();
  document.querySelectorAll('#plist .pitem').forEach(el => {
    el.style.display = !needle || el.dataset.search.includes(needle) ? '' : 'none';
  });
}

function paintProblems() {
  const list = $('#plist');
  if (!list) return;
  if (!state.problems.length) {
    mount(list, h('div', { class: 'empty', text: 'No problems yet.' }));
    return;
  }
  const nodes = state.problems.map(p => {
    const live = state.active.find(r => r.problem === p.slug);
    return h('a', {
      class: `pitem ${state.route.slug === p.slug ? 'on' : ''}`,
      href: `/problems/${p.slug}`,
      'data-search': `${p.title} ${p.slug} ${(p.tags || []).join(' ')}`.toLowerCase(),
      onclick: (e) => { e.preventDefault(); navigate(`/problems/${p.slug}`); },
    },
      h('div', { class: 'pitem-top' },
        dot(live ? live.state : p.status, !!live),
        h('span', { class: 'pitem-title', text: p.title })),
      h('div', {
        class: `pitem-sub ${live ? 'live' : ''}`,
        text: live
          ? `${live.harness} · ${usd(live.totals?.cost_computed)} · ${Math.round(live.elapsed / 60)}m`
          : (p.one_line ? p.one_line : `${p.sessions} session${p.sessions === 1 ? '' : 's'}`),
      }));
  });
  mount(list, nodes);
  const q = $('#pfilter');
  if (q && q.value) filterProblems(q.value);
}

function paintActive() {
  const list = $('#plist');
  if (!list) return;
  if (!state.active.length) {
    mount(list, h('div', { class: 'empty', text: 'Nothing running.' }));
    return;
  }
  mount(list, state.active.map(r => h('a', {
    class: 'pitem', href: `/runs/${r.run_id}`,
    onclick: (e) => { e.preventDefault(); navigate(`/runs/${r.run_id}`); },
  },
    h('div', { class: 'pitem-top' }, dot(r.state, true),
      h('span', { class: 'pitem-title', text: r.problem_title || r.problem })),
    h('div', { class: 'pitem-sub live' },
      `${r.harness} · ${usd(r.totals?.cost_computed)} · ${Math.round(r.elapsed / 60)}m`))));
}

// ------------------------------------------------------------------- header

function paintAccounts() {
  const bar = $('#accountbar');
  if (!bar) return;
  clear(bar);
  for (const a of state.accounts) {
    const w5 = (a.windows || []).find(w => w.label === '5h');
    const frac = w5?.fraction;
    const pct = frac === null || frac === undefined ? null : Math.round(frac * 100);
    const cls = a.limited ? 'chip bad' : (pct !== null && pct >= 80 ? 'chip warn' : 'chip');
    const label = a.limited
      ? `${a.id} · limited`
      : pct !== null ? `${a.id} · ${pct}%` : `${a.id} · ${usd(w5?.spend)}`;
    bar.append(h('a', {
      class: cls, href: '/accounts', title: accountTitle(a),
      onclick: (e) => { e.preventDefault(); navigate('/accounts'); },
    }, h('span', { class: 'dot', style: { background: a.color } }), label));
  }
}

function accountTitle(a) {
  const bits = [`${a.label}`];
  for (const w of a.windows || []) {
    bits.push(`${w.label}: ${usd(w.spend)}${w.cap ? ` / ${usd(w.cap)}` : ' (no cap set)'}`);
  }
  if (a.provider_window) {
    bits.push(`provider: ${a.provider_window.status} (${a.provider_window.window})`);
  }
  if (a.limited_until) bits.push(`limited until ${a.limited_until}`);
  return bits.join('\n');
}

function paintHealth() {
  const el = $('#health');
  if (!el || !state.config) return;
  const report = state.config.startup_report;
  if (!report || report.worst === 'ok') { clear(el); return; }
  const bad = (report.checks || []).filter(c => c.level !== 'ok');
  mount(el, h('a', {
    class: report.worst === 'fail' ? 'chip bad' : 'chip warn',
    href: '/accounts', title: bad.map(c => `${c.name}: ${c.detail}`).join('\n'),
    onclick: (e) => { e.preventDefault(); navigate('/accounts'); },
  }, `${bad.length} check${bad.length === 1 ? '' : 's'}`));
}

function paintTitle() {
  const n = state.active.length;
  const cost = state.active.reduce((s, r) => s + (r.totals?.cost_computed || 0), 0);
  document.title = n
    ? `◉ ${n} running · ${usd(cost)} — Atelier`
    : 'Atelier';
}

// -------------------------------------------------------------- live refresh

export async function refreshActive() {
  try {
    state.active = await api.activeRuns();
  } catch { return; }
  paintTitle();
  if (state.route.name === 'problems') paintProblems(); else paintActive();
}

export async function refreshProblems() {
  try { state.problems = await api.problems(); } catch { return; }
  if (state.route.name === 'problems') paintProblems();
}

export async function refreshAccounts() {
  try { state.accounts = await api.accounts(); } catch { return; }
  paintAccounts();
}

// ------------------------------------------------------------ new problem

async function newProblemDialog() {
  const slug = h('input', { type: 'text', placeholder: 'collatz-subsums' });
  const title = h('input', { type: 'text', placeholder: 'Sub-sums along Collatz trajectories' });
  const statement = h('textarea', { placeholder: '# Title\n\nState the problem here…',
                                    style: { minHeight: '160px' } });
  const source = h('input', { type: 'file', accept: '.pdf,application/pdf' });
  const sourceNote = h('div', {
    class: 'small muted',
    text: 'Optional. The PDF is saved as progress/source.pdf for Deslopify.',
    style: { marginTop: '4px' },
  });
  const dlg = h('dialog', {},
    h('div', { class: 'dlg-head' }, h('h2', { text: 'New problem' })),
    h('div', { class: 'dlg-body' },
      h('label', { class: 'field' }, h('span', { text: 'Slug (folder name)' }), slug),
      h('label', { class: 'field' }, h('span', { text: 'Title' }), title),
      h('label', { class: 'field' }, h('span', { text: 'Statement (problem.md)' }), statement),
      h('label', { class: 'field' }, h('span', { text: 'Source paper (PDF)' }),
        source, sourceNote)),
    h('div', { class: 'dlg-foot' },
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Cancel'),
      h('button', {
        class: 'btn primary',
        onclick: async () => {
          try {
            const form = new FormData();
            form.set('slug', slug.value.trim());
            form.set('title', title.value.trim());
            form.set('statement', statement.value);
            if (source.files[0]) form.set('source', source.files[0]);
            const r = await api.createProblem(form);
            dlg.close();
            await refreshProblems();
            navigate(`/problems/${r.slug}`);
          } catch (e) { toast(e.message, 'err'); }
        },
      }, 'Create')));
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.showModal();
  slug.focus();
}

// ---------------------------------------------------------------- bootstrap

function setupTheme() {
  const saved = localStorage.getItem('atelier-theme') || '';
  document.documentElement.dataset.theme = saved;
  $('#theme-toggle').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme;
    const next = cur === 'dark' ? 'light' : cur === 'light' ? '' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('atelier-theme', next);
  });
}

function setupKeys() {
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if ((e.metaKey || e.ctrlKey) && e.key === 'j') {
      e.preventDefault(); $('#theme-toggle').click(); return;
    }
    if (typing) return;
    if (e.key === '/') { e.preventDefault(); $('#pfilter')?.focus(); }
    if (e.key === 'g') { window.__gPrefix = true; setTimeout(() => { window.__gPrefix = false; }, 700); }
    else if (window.__gPrefix) {
      window.__gPrefix = false;
      if (e.key === 'p') navigate('/problems');
      if (e.key === 'r') navigate('/runs');
      if (e.key === 'h') navigate('/harnesses');
      if (e.key === 'a') navigate('/accounts');
    }
  });
}

function reportErrors() {
  // keepalive: sent even as the page goes away, as a beacon would be — but
  // with the launch key, which a beacon cannot carry.
  const send = (payload) => {
    try {
      fetch('/api/client-error', {
        method: 'POST', keepalive: true,
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      }).catch(() => { /* never let the reporter itself break the page */ });
    } catch { /* never let the reporter itself break the page */ }
  };
  window.addEventListener('error', (e) => send({
    message: e.message, source: e.filename, line: e.lineno, stack: e.error?.stack,
  }));
  window.addEventListener('unhandledrejection', (e) => send({
    message: String(e.reason?.message || e.reason), stack: e.reason?.stack,
  }));
}

async function boot() {
  reportErrors();
  setupTheme();
  setupKeys();
  window.addEventListener('popstate', render);

  document.querySelectorAll('nav.tabs a').forEach(a => a.addEventListener('click', (e) => {
    e.preventDefault(); navigate(a.getAttribute('href'));
  }));

  try { state.config = await api.config(); } catch (e) { toast(e.message, 'err'); }
  paintHealth();

  await Promise.all([refreshProblems(), refreshActive(), refreshAccounts()]);
  await render();

  stream.on('runs', () => { refreshActive(); });
  stream.on('problems', () => { refreshProblems(); refreshActive(); });
  stream.on('accounts', () => { refreshAccounts(); });

  // Elapsed time and cost tick even when no event arrives.
  setInterval(refreshActive, 5000);
  setInterval(refreshAccounts, 60000);
}

boot();
