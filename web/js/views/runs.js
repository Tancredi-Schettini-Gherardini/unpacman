// Runs: the global table, and the live session view with controls and steering.

import { api, stream } from '../api.js';
import { navigate, state } from '../app.js';
import {
  ago, card, clear, dot, dur, empty, h, int, meter, mount, renderDiff,
  stat, table, toast, truncate, typeset, usd,
} from '../util.js';

export async function render(root, route) {
  if (route.runId) return renderRun(root, route.runId);
  return renderTable(root);
}

// ============================================================ table

async function renderTable(root) {
  mount(root, h('div', { class: 'pad' }, empty('Loading…')));
  const runs = await api.runs('?limit=400');
  if (!runs.length) {
    mount(root, h('div', { class: 'pad' }, empty('No sessions recorded yet.')));
    return;
  }
  const total = runs.reduce((s, r) => s + (r.cost_computed || 0), 0);
  const seconds = runs.reduce((s, r) => s + (r.wall_seconds || 0), 0);

  const rows = runs.map(r => h('tr', {},
    h('td', { class: 'nowrap' }, h('a', {
      href: `/runs/${r.id}`,
      onclick: (e) => { e.preventDefault(); navigate(`/runs/${r.id}`); },
    }, h('span', { style: { display: 'inline-flex', gap: '6px', alignItems: 'center' } },
      dot(r.live ? r.live.state : r.status, !!r.live),
      new Date(r.started_at).toLocaleString(undefined,
        { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })))),
    h('td', {}, h('a', {
      href: `/problems/${r.problem}`,
      onclick: (e) => { e.preventDefault(); navigate(`/problems/${r.problem}`); },
    }, r.problem)),
    h('td', { class: 'small' }, r.harness),
    h('td', { class: 'small dim' }, r.model || '—'),
    h('td', { class: 'small dim' }, r.effort || '—'),
    h('td', { class: 'small dim' }, r.mode),
    h('td', { class: 'num small' }, dur(r.wall_seconds)),
    h('td', { class: 'num small' }, usd(r.cost_computed)),
    h('td', { class: 'num small' }, int(r.tok_out)),
    h('td', { class: 'small muted' }, truncate(r.ended_reason || r.status, 32))));

  mount(root,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { text: 'Runs' }),
        h('div', { class: 'page-sub' },
          `${runs.length} sessions · ${usd(total)} · ${dur(seconds)} of wall clock`))),
    h('div', { class: 'pad' }, table(
      [{ label: 'Started' }, { label: 'Problem' }, { label: 'Harness' }, { label: 'Model' },
       { label: 'Effort' }, { label: 'Mode' }, { label: 'Duration', num: true },
       { label: 'Cost', num: true }, { label: 'Out tok', num: true }, { label: 'Ended' }],
      rows)));
}

// ============================================================ live session

const KIND_LABEL = {
  'assistant.text': 'say', 'assistant.thinking': 'think', 'tool.use': 'tool',
  'tool.result': 'result', 'notice': 'note', 'error': 'error', 'usage': 'usage',
  'steer.sent': 'you', 'steer.delivered': 'ack', 'budget': 'budget',
  'latex': 'latex', 'run.state': 'state', 'rate_limit': 'limit',
  'run.started': 'start', 'run.ended': 'end', 'provider.raw': 'raw',
  'agent.started': 'pass', 'agent.ended': 'pass',
};

const HIDDEN = new Set(['usage', 'steer.delivered', 'run.started']);

async function renderRun(root, runId) {
  let data;
  try { data = await api.run(runId); }
  catch (e) { mount(root, h('div', { class: 'pad' }, empty(e.message))); return; }

  const rec = data.run || {};
  const live = data.live;
  const isLive = !!live && !['done', 'incomplete', 'failed', 'cancelled'].includes(live.state);

  // ---- header -----------------------------------------------------------
  const statusLine = h('div', { class: 'stats', id: 'runstats' });
  const controls = h('div', { class: 'btn-row', id: 'runctl' });

  // ---- panes ------------------------------------------------------------
  const transcript = h('div', { class: 'transcript', id: 'transcript' });
  const rightPane = h('div', { class: 'right', id: 'rightpane' });
  const steerbar = h('div', { class: 'steerbar', id: 'steerbar' });

  const left = h('div', { class: 'left' }, transcript);
  const split = h('div', { class: 'split', style: { height: 'calc(100vh - 150px)' } },
    left, rightPane);

  mount(root,
    h('div', { class: 'page-head' },
      h('div', { style: { flex: 1, minWidth: 0 } },
        h('h1', {}, h('span', { style: { display: 'inline-flex', gap: '10px', alignItems: 'center' } },
          dot(live ? live.state : rec.status, isLive),
          h('a', {
            href: `/problems/${rec.problem || live?.problem}`,
            onclick: (e) => {
              e.preventDefault(); navigate(`/problems/${rec.problem || live.problem}`);
            },
          }, live?.problem_title || rec.problem || runId))),
        h('div', { class: 'page-sub mono' },
          `${rec.harness || live?.harness} v${rec.harness_version ?? live?.harness_version ?? '?'}`
          + ` · ${rec.model || live?.model} · ${rec.effort || live?.effort}`
          + ` · ${rec.account || live?.account || '—'} · ${rec.mode || live?.mode}`)),
      controls),
    h('div', { style: { padding: '10px 24px 0', maxWidth: '1180px' } }, statusLine),
    split);

  // Codex runs one turn with no input channel, so there is nothing to steer.
  // Showing the box and failing on send would be worse than not showing it.
  const canSteer = (state.config?.providers || {})[rec.provider || live?.provider]
    ?.steering !== 'none';
  if (isLive && canSteer) left.after(steerbar);

  paintStats(statusLine, live, rec);
  paintControls(controls, runId, live, isLive, rec);
  paintRight(rightPane, data, live, rec);
  if (isLive && canSteer) paintSteer(steerbar, runId);
  else if (isLive) {
    left.after(h('div', { class: 'steerbar' },
      h('div', { class: 'small muted' },
        `${rec.provider || live?.provider} runs a session as a single turn, so a `
        + 'message cannot be delivered while it works. Stop it and start again, '
        + 'or wait for it to finish.')));
  }

  // ---- transcript -------------------------------------------------------
  let lastSeq = 0;
  const seen = new Set();

  const append = (evt) => {
    if (evt.seq && seen.has(evt.seq)) return;
    if (evt.seq) { seen.add(evt.seq); lastSeq = Math.max(lastSeq, evt.seq); }
    const node = renderEvent(evt);
    if (!node) return;
    const atBottom = left.scrollHeight - left.scrollTop - left.clientHeight < 120;
    transcript.append(node);
    typeset(node);
    if (atBottom) left.scrollTop = left.scrollHeight;
  };

  try {
    const { events } = await api.runEvents(runId, 0);
    clear(transcript);
    events.forEach(append);
    left.scrollTop = left.scrollHeight;
    if (!events.length) mount(transcript, empty('No events recorded.'));
  } catch (e) { mount(transcript, empty(e.message)); }

  if (isLive) {
    stream.on(`run:${runId}`, (msg) => {
      append(msg);
      if (msg.type === 'run.state' || msg.type === 'usage' || msg.type === 'budget') {
        api.run(runId).then(d => {
          paintStats(statusLine, d.live, d.run || {});
          paintControls(controls, runId, d.live, !!d.live &&
            !['done', 'incomplete', 'failed', 'cancelled'].includes(d.live.state), d.run || {});
          paintRight(rightPane, d, d.live, d.run || {});
          if (d.live && ['done', 'incomplete', 'failed', 'cancelled'].includes(d.live.state)) {
            steerbar.remove();
            toast(`Session ${d.live.state}`, d.live.state === 'done' ? 'ok' : 'err');
          }
        }).catch(() => {});
      }
    });
    // Elapsed keeps moving between events.
    const tick = setInterval(async () => {
      try {
        const d = await api.run(runId);
        paintStats(statusLine, d.live, d.run || {});
        if (!d.live) clearInterval(tick);
      } catch { clearInterval(tick); }
    }, 4000);
  }
}

function paintStats(box, live, rec) {
  const t = live?.totals || {
    tok_in: rec.tok_in, tok_out: rec.tok_out, turns: rec.turns,
    tool_calls: rec.tool_calls, cost_computed: rec.cost_computed,
  };
  const elapsed = live ? live.elapsed : rec.wall_seconds;
  const budget = live?.budget;
  mount(box,
    stat('Elapsed', dur(elapsed),
      live?.frozen_seconds ? h('div', { class: 'small muted', text: `${dur(live.frozen_seconds)} frozen` }) : null),
    stat('Cost', usd(t.cost_computed),
      budget?.usd ? h('div', {},
        meter(budget.fraction),
        h('div', { class: 'small muted', text: `of ${usd(budget.usd)}` })) : null),
    stat('Tokens out', int(t.tok_out)),
    stat('Tokens in', int(t.tok_in)),
    stat('Turns', int(t.turns)),
    stat('Tool calls', int(t.tool_calls)),
    live && live.pass && live.pass.of > 1
      ? stat('Pass', `${live.pass.index} of ${live.pass.of}`,
        h('div', { class: 'small muted', text: live.pass.label }))
      : null,
    live ? stat('State', live.state) : stat('Ended', rec.ended_reason || rec.status || '—'));
}

function paintControls(box, runId, live, isLive, rec) {
  clear(box);
  if (!isLive) {
    if (rec.git_commit) {
      box.append(h('button', {
        class: 'btn sm',
        onclick: () => showDiff(runId),
      }, 'View diff'));
    }
    box.append(h('button', {
      class: 'btn sm',
      onclick: () => verdictDialog(runId, rec),
    }, 'Record verdict'));
    return;
  }
  const frozen = live.state === 'frozen';
  const act = async (action, confirmMsg) => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    try {
      await api.signal(runId, action);
      toast(`${action} sent`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
  box.append(
    h('button', { class: 'btn sm', onclick: () => act('wrapup') }, 'Wrap up'),
    frozen
      ? h('button', { class: 'btn sm', onclick: () => act('thaw') }, '▶ Thaw')
      : h('button', {
          class: 'btn sm',
          title: 'True pause (SIGSTOP). Safe for short holds; an in-flight request '
               + 'may time out if frozen for more than about a minute.',
          onclick: () => act('freeze'),
        }, '‖ Freeze'),
    h('button', {
      class: 'btn sm',
      title: 'Graceful interrupt. The conversation stays resumable by session id.',
      onclick: () => act('stop', 'Stop this session? It will be resumable.'),
    }, '■ Checkpoint & stop'),
    h('button', {
      class: 'btn sm danger',
      title: 'Kill the process and revert everything this session wrote.',
      onclick: () => act('abandon',
        'Abandon: kill the session AND revert every change it made to the problem folder. Continue?'),
    }, '⤫ Abandon'));
}

function paintSteer(bar, runId) {
  const ta = h('textarea', {
    placeholder: 'Steer the session… (⌘↵ to send)', rows: 1,
    onkeydown: (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
    },
  });
  const send = async (text) => {
    const message = (text ?? ta.value).trim();
    if (!message) return;
    try {
      const r = await api.steer(runId, message);
      if (!text) ta.value = '';
      toast(r.delivered ? 'Delivered' : 'Queued', r.delivered ? 'ok' : '');
    } catch (e) { toast(e.message, 'err'); }
  };
  const preset = (label, message) =>
    h('button', { class: 'btn sm', onclick: () => send(message) }, label);

  mount(bar,
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px', flex: 1 } },
      h('div', { class: 'btn-row' },
        preset('Wrap up',
          'Please wrap up now: stop new work, finish what is in progress, then update '
          + 'progress/math.tex and progress/state.json and write progress/_session_notes.md.'),
        preset("You're circling",
          'You appear to be going in circles. Stop, write down in one sentence what you '
          + 'have actually established, and pick a different line of attack.'),
        preset('Verify that',
          'Before continuing, verify the claim you just made. Try to refute it: check '
          + 'quantifier order, edge cases, and every step you called obvious.')),
      ta),
    h('button', { class: 'btn primary', onclick: () => send() }, 'Send'));
}

function paintRight(pane, data, live, rec) {
  const files = data.files || {};
  const steering = data.steering || [];
  const nodes = [];

  if (live?.totals?.tool_breakdown && Object.keys(live.totals.tool_breakdown).length) {
    nodes.push(card('Tool use',
      h('div', { class: 'small' },
        Object.entries(live.totals.tool_breakdown)
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => h('div', {
            style: { display: 'flex', justifyContent: 'space-between' },
          }, h('span', { text: k }), h('span', { class: 'num muted', text: String(v) }))))));
  }

  if (steering.length) {
    nodes.push(card('Steering',
      h('div', { class: 'small' }, steering.map(s => h('div', { style: { margin: '6px 0' } },
        h('div', { class: 'muted', style: { fontSize: '11px' } },
          `${s.origin} · ${s.delivered_at ? 'delivered' : 'queued'}`),
        h('div', { text: truncate(s.text, 160) }))))));
  }

  const runId = data.run?.id || live?.run_id;
  const fileUrl = (name) =>
    `/api/runs/${runId}/file?name=${encodeURIComponent(name)}`;

  // A reading run owns its companion; a one-shot may own math.pdf; answer.pdf
  // is the fallback typeset by the app for a tool-less harness.
  const doc = files['reading/companion.pdf'] ? 'reading/companion.pdf'
    : files['math.pdf'] ? 'math.pdf' : files['answer.pdf'] ? 'answer.pdf' : null;
  if (doc) {
    const actions = [
      h('a', { class: 'btn sm', href: fileUrl(doc), target: '_blank' }, 'Open full size'),
    ];
    nodes.push(card('Document',
      h('iframe', {
        src: fileUrl(doc),
        style: { width: '100%', height: '320px', border: '1px solid var(--border)',
                 borderRadius: '6px', background: '#fff' },
      }),
      h('div', { class: 'btn-row', style: { marginTop: '8px' } }, actions),
      doc === 'answer.pdf'
        ? h('div', { class: 'small muted', style: { marginTop: '6px' } },
          'Typeset by atelier from the session’s answer: this harness has no '
          + 'filesystem, so it never wrote a document of its own.')
        : null));
  } else if (files['answer.md']) {
    nodes.push(card('Answer', h('div', { class: 'btn-row' },
      h('a', { class: 'btn sm', href: fileUrl('answer.md'), target: '_blank' },
        'Read answer.md'))));
  }

  const fileLinks = Object.keys(files).filter(
    n => !['events.jsonl', 'math.pdf', 'answer.pdf', doc].includes(n));
  if (fileLinks.length) {
    nodes.push(card('Run artefacts',
      h('div', { class: 'small' }, fileLinks.map(name => h('div', {},
        h('a', { href: fileUrl(name), target: '_blank', text: name }))))));
  }

  nodes.push(card('Run directory', h('div', { class: 'mono small muted', text: data.run_dir })));

  if (rec.error) nodes.push(card('Error', h('div', { class: 'small err', text: rec.error })));

  if (rec.id && !live && files['answer.md'] === undefined && !rec.tool_calls
      && rec.tok_out > 0) {
    nodes.push(card('No output',
      h('div', { class: 'small warn' },
        `This session spent ${int(rec.tok_out)} output tokens without producing `
        + 'any text. At high effort on a hard problem the model can hit its output '
        + 'ceiling while still reasoning. Lower the effort, raise the budget, or use '
        + 'a harness with tools so partial work reaches disk as it goes.')));
  }

  mount(pane, nodes);
}

// ---------------------------------------------------------------- rendering

function renderEvent(evt) {
  const kind = evt.type;
  if (HIDDEN.has(kind)) return null;
  const p = evt.payload || {};
  const time = new Date(evt.ts).toLocaleTimeString(undefined,
    { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const label = KIND_LABEL[kind] || kind;
  const agent = evt.agent && evt.agent !== 'lead' ? `[${evt.agent}] ` : '';

  const head = (line, cls = '') => h('div', { class: 'ev-head' },
    h('time', { text: time }),
    h('span', { class: 'ev-kind', text: label }),
    h('span', { class: `ev-line ${cls}`, text: agent + line }));

  switch (kind) {
    case 'assistant.text': {
      const el = h('div', { class: 'ev' },
        h('div', { class: 'ev-head' },
          h('time', { text: time }),
          h('span', { class: 'ev-kind', text: agent ? `say ${agent}` : 'say' })),
        h('div', { class: 'ev-body prose', html: mdish(p.text || '') }));
      return el;
    }
    case 'assistant.thinking': {
      const el = h('div', { class: 'ev' },
        h('details', {},
          h('summary', { text: `${time}  thinking (${(p.text || '').length} chars)` }),
          h('div', { class: 'ev-body thinking prose', html: mdish(p.text || '') })));
      return el;
    }
    case 'tool.use': {
      const input = JSON.stringify(p.input || {});
      const el = h('div', { class: 'ev tool' },
        head(`${p.name} ${truncate(input, 150)}`));
      if (input.length > 150) {
        el.append(h('details', {},
          h('summary', { text: 'input' }),
          h('pre', { class: 'raw', text: JSON.stringify(p.input, null, 2) })));
      }
      return el;
    }
    case 'tool.result': {
      const text = String(p.content || '');
      if (!text.trim()) return null;
      const el = h('div', { class: `ev ${p.is_error ? 'err' : ''}` },
        head(truncate(text, 150)));
      if (text.length > 150) {
        el.append(h('details', {},
          h('summary', { text: 'output' }),
          h('pre', { class: 'raw', text: text.slice(0, 20000) })));
      }
      return el;
    }
    case 'steer.sent':
      return h('div', { class: 'ev steer' },
        head(p.text || '', ''), );
    case 'budget':
      return h('div', { class: 'ev budget' },
        head(`${p.level}: ${p.detail || ''}`));
    case 'error':
      return h('div', { class: 'ev err' }, head(truncate(p.message || JSON.stringify(p), 300)));
    case 'latex':
      return h('div', { class: 'ev' },
        head(p.ok ? 'math.pdf rebuilt' : `compile failed — ${(p.errors || []).slice(0, 2).join(' · ')}`,
          p.ok ? '' : 'err'));
    case 'rate_limit': {
      // `allowed_warning` is a threshold notice, not a refusal. Reading it as a
      // block makes a healthy session look like it was cut off.
      const blocked = p.status && !String(p.status).startsWith('allowed');
      const pct = p.raw && typeof p.raw.utilization === 'number'
        ? ` · ${Math.round(p.raw.utilization * 100)}% used` : '';
      const when = p.resets_at_epoch
        ? ` · resets ${new Date(p.resets_at_epoch * 1000).toLocaleString()}` : '';
      return h('div', { class: `ev ${blocked ? 'err' : 'notice'}` },
        head(`${(p.window || 'window').replace('_', '-')} allowance: `
          + `${blocked ? 'blocked' : 'still allowed'}${pct}${when}`,
        blocked ? 'err' : ''));
    }
    case 'agent.started':
      return h('div', { class: 'ev notice' },
        head(`— ${p.label} begins${p.budget_usd ? ` · up to ${usd(p.budget_usd)}` : ''} —`));
    case 'agent.ended': {
      const bits = [];
      if (p.findings !== undefined && p.findings !== null) {
        bits.push(`${p.findings} finding${p.findings === 1 ? '' : 's'}`
          + (p.blocking ? `, ${p.blocking} blocking` : ''));
      }
      if (p.diff) bits.push(p.diff);
      if (p.summary) bits.push(p.summary);
      if (p.stop) bits.push(`loop ends: ${p.stop}`);
      return h('div', { class: 'ev notice' },
        head(`— ${p.label} ends${bits.length ? ` · ${bits.join(' · ')}` : ''} —`));
    }
    case 'run.state':
      return h('div', { class: 'ev notice' }, head(`${p.state}${p.reason ? ` — ${p.reason}` : ''}`));
    case 'run.ended':
      return h('div', { class: 'ev notice' },
        head(`ended: ${p.reason} · ${usd(p.stats?.cost_computed)} · ${dur(p.stats?.wall_seconds)}`));
    case 'notice':
      return h('div', { class: 'ev notice' }, head(p.message || ''));
    case 'provider.raw':
      return null;
    default:
      return h('div', { class: 'ev notice' }, head(truncate(JSON.stringify(p), 160)));
  }
}

/** Minimal inline markdown: enough for agent prose, cheap, and safe.
 *  Full markdown is rendered server-side wherever the content is durable. */
function mdish(text) {
  const escaped = String(text)
    .replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return escaped
    .replace(/```([\s\S]*?)```/g, (_, code) => `<pre>${code.trim()}</pre>`)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\s)\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/^/, '<p>').replace(/$/, '</p>');
}

// ------------------------------------------------------------------ dialogs

async function showDiff(runId) {
  const body = h('div', { class: 'dlg-body' }, empty('Loading…'));
  const dlg = h('dialog', { class: 'wide' },
    h('div', { class: 'dlg-head' }, h('h2', { text: 'Session diff' })),
    body,
    h('div', { class: 'dlg-foot' },
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')));
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.showModal();
  try {
    const r = await api.runDiff(runId);
    mount(body, r.diff ? renderDiff(r.diff) : empty(r.note || 'No changes.'));
  } catch (e) { mount(body, empty(e.message)); }
}

function verdictDialog(runId, rec) {
  const options = ['solved', 'substantial', 'marginal', 'nothing', 'wrong'];
  let choice = null;
  const seg = h('div', { class: 'seg' }, options.map(o => h('button', {
    text: o,
    onclick: (e) => {
      choice = o;
      [...e.target.parentElement.children].forEach(b => b.classList.toggle('on', b === e.target));
    },
  })));
  const comment = h('textarea', { placeholder: 'What did this session actually achieve?' });
  const dlg = h('dialog', {},
    h('div', { class: 'dlg-head' }, h('h2', { text: 'Record your verdict' })),
    h('div', { class: 'dlg-body' },
      h('p', { class: 'small muted', style: { marginBottom: '12px' } },
        'Your judgement, not the model\'s. This is what drives the harness comparison table.'),
      h('label', { class: 'field' }, h('span', { text: 'Verdict' }), seg),
      h('label', { class: 'field' }, h('span', { text: 'Comment' }), comment)),
    h('div', { class: 'dlg-foot' },
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Cancel'),
      h('button', {
        class: 'btn primary',
        onclick: async () => {
          try {
            await api.verdict(runId, { verdict: choice, comment: comment.value });
            toast('Verdict recorded', 'ok');
            dlg.close();
          } catch (e) { toast(e.message, 'err'); }
        },
      }, 'Save')));
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.showModal();
}
