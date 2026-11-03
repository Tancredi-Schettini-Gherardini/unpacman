// Problems: the list landing page and the per-problem workspace.

import { api } from '../api.js';
import { launchDialog } from '../launch.js';
import { navigate, refreshProblems, state } from '../app.js';
import {
  ago, card, clear, dot, dur, empty, esc, h, int, meter, mount, renderDiff,
  stat, table, toast, truncate, typeset, usd,
} from '../util.js';

export async function render(root, route) {
  if (!route.slug) return renderList(root);
  return renderProblem(root, route.slug);
}

// ============================================================ list

function renderList(root) {
  if (!state.problems.length) {
    mount(root, h('div', { class: 'pad' },
      empty('No problems yet. Create one from the sidebar, or run `atelier import <path>`.')));
    return;
  }
  const totalCost = state.problems.reduce((s, p) => s + (p.total_cost || 0), 0);
  const totalSessions = state.problems.reduce((s, p) => s + (p.sessions || 0), 0);

  const rows = state.problems.map(p => {
    const live = state.active.find(r => r.problem === p.slug);
    return h('tr', {},
      h('td', {}, h('a', {
        href: `/problems/${p.slug}`,
        onclick: (e) => { e.preventDefault(); navigate(`/problems/${p.slug}`); },
      }, h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '8px' } },
        dot(live ? live.state : p.status, !!live), p.title))),
      h('td', { class: 'small dim' }, live ? `running · ${live.harness}` : p.status),
      h('td', { class: 'small muted' }, truncate(p.one_line, 90) || '—'),
      h('td', { class: 'num small' }, int(p.sessions)),
      h('td', { class: 'num small' }, usd(p.total_cost)),
      h('td', { class: 'num small muted nowrap' }, ago(p.last_session)));
  });

  mount(root,
    h('div', { class: 'page-head' },
      h('div', { style: { flex: 1 } },
        h('h1', { text: 'Problems' }),
        h('div', { class: 'page-sub' },
          `${state.problems.length} problems · ${totalSessions} sessions · ${usd(totalCost)} total`))),
    h('div', { class: 'pad' },
      table(
        [{ label: 'Problem' }, { label: 'Status' }, { label: 'Current state' },
         { label: 'Sessions', num: true }, { label: 'Cost', num: true },
         { label: 'Last', num: true }],
        rows)));
}

// ============================================================ detail

const TABS = ['Overview', 'PDF', 'Lab book', 'Files', 'Sessions', 'Diff'];

function tabsFor(data, live) {
  // The generic PDF tab is progress/math.pdf, which Deslopify neither reads
  // nor writes. Its source, summary, and proof map belong under Files instead.
  // Runs arrive newest first, so an idle problem follows its latest workflow.
  const harness = live?.harness || data.runs?.[0]?.harness;
  return harness?.startsWith('deslopify')
    ? TABS.filter(tab => tab !== 'PDF') : TABS;
}

async function renderProblem(root, slug) {
  let data;
  try { data = await api.problem(slug); }
  catch (e) { mount(root, h('div', { class: 'pad' }, empty(e.message))); return; }

  const live = state.active.find(r => r.problem === slug) || data.active_run;
  const st = data.state || {};
  const pane = h('div', { class: 'pad', id: 'ppane' });
  const tabs = tabsFor(data, live);

  const key = `atelier-tab-${slug}`;
  let current = sessionStorage.getItem(key) || 'Overview';
  if (!tabs.includes(current)) current = 'Overview';

  const tabbar = h('div', { class: 'tabbar' }, tabs.map(t =>
    h('button', {
      class: t === current ? 'on' : '', text: t,
      onclick: (e) => {
        current = t;
        sessionStorage.setItem(key, t);
        [...e.target.parentElement.children].forEach(b => b.classList.toggle('on', b === e.target));
        paint();
      },
    })));

  function paint() {
    clear(pane);
    switch (current) {
      case 'Overview': return paintOverview(pane, data, st, live);
      case 'PDF': return paintPdf(pane, data);
      case 'Lab book': return paintLabbook(pane, data);
      case 'Files': return paintFiles(pane, data);
      case 'Sessions': return paintSessions(pane, data);
      case 'Diff': return paintDiff(pane, data);
    }
  }

  const actions = h('div', { class: 'btn-row' });
  if (live) {
    actions.append(h('button', {
      class: 'btn primary',
      onclick: () => navigate(`/runs/${live.run_id}`),
    }, '◉ Open live session'));
  }
  actions.append(h('button', {
    class: live ? 'btn' : 'btn primary',
    onclick: () => launchDialog({ problem: slug, onLaunched: (id) => navigate(`/runs/${id}`) }),
  }, '▶ New session'));
  if (data.lock && !live) {
    actions.append(h('button', {
      class: 'btn danger sm',
      title: `Locked by ${data.lock.run_id} (pid ${data.lock.pid}) since ${data.lock.started_at}`,
      onclick: async () => {
        if (!confirm('Force-release the lock on this problem?')) return;
        await api.unlock(slug); toast('Lock released', 'ok'); navigate(`/problems/${slug}`);
      },
    }, 'Force unlock'));
  }

  mount(root,
    h('div', { class: 'page-head' },
      h('div', { style: { flex: 1, minWidth: 0 } },
        h('h1', {}, h('span', {
          style: { display: 'inline-flex', alignItems: 'center', gap: '10px' },
        }, dot(live ? live.state : st.status, !!live), data.title)),
        h('div', { class: 'page-sub' },
          [(data.meta.tags || []).join(' · '),
           `${data.runs.length} session${data.runs.length === 1 ? '' : 's'}`,
           usd(data.runs.reduce((s, r) => s + (r.cost_computed || 0), 0)),
           data.meta.difficulty].filter(Boolean).join('  ·  '))),
      actions),
    tabbar, pane);

  paint();
}

// ---------------------------------------------------------------- overview

function paintOverview(pane, data, st, live) {
  const conf = st.confidence;
  const listCard = (title, items, fmt) => {
    if (!items || !items.length) return null;
    return card(title, h('ul', { style: { paddingLeft: '18px', fontSize: '13px' } },
      items.slice(0, 25).map(i => h('li', { style: { margin: '4px 0' } }, fmt(i)))));
  };
  const asText = (i) => typeof i === 'string' ? i
    : `${i.id ? `[${i.id}] ` : ''}${i.statement || i.approach || JSON.stringify(i)}`
      + (i.why ? ` — ${i.why}` : '');

  const summary = card('Current state',
    h('div', { class: 'prose', style: { fontSize: '15px', maxWidth: 'none' } },
      h('p', { text: st.one_line || 'No summary recorded yet.' })),
    h('div', { class: 'stats', style: { marginTop: '14px' } },
      stat('Status', st.status || 'open'),
      stat('Confidence', conf === undefined || conf === null ? '—' : `${Math.round(conf * 100)}%`,
        conf === undefined || conf === null ? null : meter(conf)),
      stat('Established', int((st.established || []).length)),
      stat('Conjectures', int((st.conjectures || []).length)),
      stat('Updated', st.updated ? ago(st.updated) : '—')));

  const liveCard = live ? card('Session in progress',
    h('div', { class: 'stats' },
      stat('Harness', live.harness),
      stat('Model', live.model),
      stat('Elapsed', dur(live.elapsed)),
      stat('Cost', usd(live.totals?.cost_computed),
        live.budget?.usd ? meter(live.budget.fraction) : null),
      stat('Turns', int(live.totals?.turns))),
    h('div', { style: { marginTop: '12px' } },
      h('button', { class: 'btn sm', onclick: () => navigate(`/runs/${live.run_id}`) },
        'Open live view →'))) : null;

  const blockers = listCard('Blockers', st.blockers, asText);
  const next = listCard('Next steps', st.next_steps, asText);
  const est = listCard('Established', st.established, asText);
  const conj = listCard('Conjectures', st.conjectures, asText);
  const dead = listCard('Dead ends — do not repeat', st.dead_ends, asText);

  const nodes = [liveCard, summary];
  if (blockers || next) nodes.push(h('div', { class: 'grid2' }, blockers, next));
  if (est || conj) nodes.push(h('div', { class: 'grid2' }, est, conj));
  if (dead) nodes.push(dead);

  mount(pane, nodes.filter(Boolean));
  typeset(pane);
}

// --------------------------------------------------------------------- pdf

function paintPdf(pane, data) {
  // This tab is the problem's own document and nothing else: the one that
  // standard sessions edit, rebuilt whenever math.tex changes. One-shot
  // sessions are independent attempts that never touch the problem folder, so
  // each keeps its own PDF and they are listed under Sessions. Showing one of
  // them here would make a blind attempt look like the problem's state.
  if (!data.has_pdf) {
    const oneshots = (data.runs || []).filter(r => r.mode === 'oneshot' && r.pdf).length;
    mount(pane,
      empty('No document yet. This is the problem\u2019s own PDF, built from '
        + 'progress/math.tex whenever a standard session changes it.'),
      h('div', { class: 'small muted', style: { textAlign: 'center', marginTop: '-8px' } },
        oneshots
          ? `${oneshots} one-shot session${oneshots === 1 ? ' has' : 's have'} produced a `
            + 'document of their own. They are independent attempts, so each is kept '
            + 'separately under Sessions.'
          : 'One-shot sessions keep their own PDF under Sessions instead.'),
      h('div', { style: { textAlign: 'center', marginTop: '12px' } },
        h('button', { class: 'btn', onclick: () => compile(data.slug) }, 'Compile now')));
    return;
  }
  const url = api.fileUrl(data.slug, 'progress/math.pdf') + `&t=${Date.now()}`;
  mount(pane,
    h('div', { class: 'btn-row', style: { marginBottom: '10px' } },
      h('button', { class: 'btn sm', onclick: () => compile(data.slug) }, 'Recompile'),
      h('button', {
        class: 'btn sm',
        onclick: () => api.openExternally(data.slug, 'progress/math.pdf')
          .then(() => toast('Opened in Preview', 'ok')).catch(e => toast(e.message, 'err')),
      }, 'Open in Preview'),
      h('a', { class: 'btn sm', href: url, download: `${data.slug}.pdf` }, 'Download')),
    h('iframe', { class: 'pdfframe', src: url }));
}

async function compile(slug) {
  toast('Compiling…');
  try {
    const r = await api.compile(slug);
    if (r.ok) { toast('Compiled', 'ok'); navigate(`/problems/${slug}`); }
    else toast(r.skipped || (r.errors || []).slice(0, 2).join(' · ') || 'Compile failed', 'err');
  } catch (e) { toast(e.message, 'err'); }
}

// ---------------------------------------------------------------- lab book

async function paintLabbook(pane, data) {
  mount(pane, empty('Loading…'));
  try {
    const f = await api.file(data.slug, 'progress/labbook.md');
    const box = h('div', { class: 'prose wide', html: f.html });
    mount(pane, box);
    typeset(box);
  } catch (e) {
    mount(pane, empty(`No lab book yet (${e.message}).`));
  }
}

// ------------------------------------------------------------------- files

async function paintFiles(pane, data) {
  const listBox = h('div', { class: 'filelist' });
  const viewer = h('div', { style: { minHeight: '300px' } },
    empty('Select a file.'));
  mount(pane, h('div', { class: 'split', style: { height: 'auto' } },
    h('div', { class: 'left', style: { padding: 0 } }, viewer),
    h('div', { class: 'right', style: { padding: '10px' } }, listBox)));

  const files = await api.files(data.slug);
  const dirs = [''];
  const painted = new Set();

  async function paintDir(subdir, container, depth) {
    if (painted.has(subdir)) return;
    painted.add(subdir);
    const entries = subdir ? await api.files(data.slug, subdir) : files;
    for (const f of entries) {
      const row = h('div', {
        class: 'filerow',
        style: { paddingLeft: `${8 + depth * 12}px` },
        onclick: async () => {
          document.querySelectorAll('.filerow').forEach(r => r.classList.remove('on'));
          row.classList.add('on');
          if (f.is_dir) {
            const holder = h('div');
            row.after(holder);
            await paintDir(f.path, holder, depth + 1);
            return;
          }
          await showFile(data.slug, f, viewer);
        },
      },
        h('span', { class: 'fname', text: (f.is_dir ? '▸ ' : '') + f.name }),
        f.is_dir ? null : h('span', { class: `badge ${f.kind}`, text: f.kind }),
        f.is_dir ? null : h('span', { class: 'muted small', text: f.size_human || '' }));
      container.append(row);
    }
  }
  clear(listBox);
  await paintDir('', listBox, 0);
}

async function showFile(slug, f, viewer) {
  if (f.ext === 'pdf') {
    mount(viewer, h('iframe', { class: 'pdfframe', src: api.fileUrl(slug, f.path) }));
    return;
  }
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(f.ext)) {
    mount(viewer, h('img', { src: api.fileUrl(slug, f.path), style: { maxWidth: '100%' } }));
    return;
  }
  mount(viewer, empty('Loading…'));
  try {
    const r = await api.file(slug, f.path);
    const head = h('div', {
      style: { display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '10px' },
    },
      h('span', { class: 'mono', text: f.path }),
      h('span', { class: `badge ${r.classification}`, text: r.classification }),
      h('span', { class: 'spacer', style: { flex: 1 } }),
      h('button', {
        class: 'btn sm',
        onclick: () => api.openExternally(slug, f.path).catch(e => toast(e.message, 'err')),
      }, 'Open externally'));
    const body = h('div', { class: r.kind === 'markdown' ? 'prose wide' : '', html: r.html });
    mount(viewer, head, body);
    typeset(body);
  } catch (e) { mount(viewer, empty(e.message)); }
}

// ---------------------------------------------------------------- sessions

function paintSessions(pane, data) {
  if (!data.runs.length) { mount(pane, empty('No sessions yet.')); return; }

  // Each one-shot session keeps its own document. They are separate attempts at
  // the same problem, so they are never merged or overwritten — the point of
  // running one blind is to see what a method reaches unaided, and that is only
  // answerable if every attempt survives on its own.
  const preview = h('div', { style: { marginTop: '14px' } });
  const showDocument = (run) => {
    const url = `/api/runs/${encodeURIComponent(run.id)}/file?name=${encodeURIComponent(run.pdf.name)}`;
    mount(preview, card(
      `${run.mode === 'oneshot' ? 'One-shot' : 'Session'} · `
      + `${new Date(run.started_at).toLocaleString()} · ${run.harness} · ${run.model || '—'}`,
      h('div', { class: 'btn-row', style: { marginBottom: '10px' } },
        h('a', { class: 'btn sm', href: url, target: '_blank' }, 'Open full size'),
        h('button', {
          class: 'btn sm',
          onclick: () => navigate(`/runs/${run.id}`),
        }, 'Session record'),
        h('button', { class: 'btn sm', onclick: () => mount(preview) }, 'Close')),
      h('iframe', {
        src: url,
        style: {
          width: '100%', height: '70vh', border: '1px solid var(--border)',
          borderRadius: '6px', background: '#fff',
        },
      })));
    preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const documentCell = (r) => {
    if (r.pdf) {
      return h('button', {
        class: 'btn sm', onclick: () => showDocument(r),
        title: `${r.pdf.name} · ${Math.round(r.pdf.size / 1024)} KB`,
      }, 'View PDF');
    }
    if (r.mode === 'oneshot') return h('span', { class: 'muted small' }, '—');
    // Standard sessions edit the problem's own document rather than keeping one.
    return h('span', { class: 'muted small', title: "edits the problem's own PDF" },
      'in PDF tab');
  };

  const rows = data.runs.map(r => h('tr', {},
    h('td', { class: 'nowrap' }, h('a', {
      href: `/runs/${r.id}`,
      onclick: (e) => { e.preventDefault(); navigate(`/runs/${r.id}`); },
    }, new Date(r.started_at).toLocaleString(undefined,
      { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))),
    h('td', {}, h('span', { style: { display: 'inline-flex', gap: '6px', alignItems: 'center' } },
      dot(r.status), r.harness)),
    h('td', { class: 'small dim' }, `${r.model || '—'} · ${r.effort || '—'}`),
    h('td', { class: 'small dim' }, r.mode),
    h('td', { class: 'num small' }, dur(r.wall_seconds)),
    h('td', { class: 'num small' }, usd(r.cost_computed)),
    h('td', { class: 'num small' }, int(r.tok_out)),
    h('td', { class: 'small' },
      r.status_before && r.status_after && r.status_before !== r.status_after
        ? h('span', { class: 'ok' }, `${r.status_before} → ${r.status_after}`)
        : h('span', { class: 'muted' }, r.status_after || '—')),
    h('td', { class: 'small muted' }, truncate(r.ended_reason, 40)),
    h('td', { class: 'nowrap' }, documentCell(r))));

  const withDocs = data.runs.filter(r => r.mode === 'oneshot' && r.pdf).length;
  mount(pane,
    withDocs
      ? h('div', { class: 'small muted', style: { marginBottom: '10px' } },
        `${withDocs} independent one-shot document${withDocs === 1 ? '' : 's'}. `
        + 'Each is kept exactly as that session produced it.')
      : null,
    table(
      [{ label: 'Started' }, { label: 'Harness' }, { label: 'Model' }, { label: 'Mode' },
       { label: 'Duration', num: true }, { label: 'Cost', num: true },
       { label: 'Out tok', num: true }, { label: 'Status' }, { label: 'Ended' },
       { label: 'Document' }],
      rows),
    preview);
}

// -------------------------------------------------------------------- diff

function paintDiff(pane, data) {
  const withCommits = data.runs.filter(r => r.git_commit);
  if (!withCommits.length) {
    mount(pane, empty('No committed sessions yet.'));
    return;
  }
  const sel = h('select', {},
    withCommits.map(r => h('option', { value: r.id },
      `${new Date(r.started_at).toLocaleString()} · ${r.harness}`)));
  const out = h('div', { style: { marginTop: '12px' } });
  const load = async () => {
    mount(out, empty('Loading…'));
    try {
      const r = await api.runDiff(sel.value);
      mount(out, r.diff ? renderDiff(r.diff) : empty(r.note || 'No changes.'));
    } catch (e) { mount(out, empty(e.message)); }
  };
  sel.addEventListener('change', load);
  mount(pane,
    h('label', { class: 'field', style: { maxWidth: '420px' } },
      h('span', { text: 'Session' }), sel),
    out);
  load();
}
