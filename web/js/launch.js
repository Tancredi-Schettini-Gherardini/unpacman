// The New Session dialog: harness, mode, account, model, effort, task, budget,
// with a preview of the exact compiled prompt before anything is spent.

import { api } from './api.js';
import { state } from './app.js';
import { clear, empty, h, mount, toast, usd } from './util.js';

export async function launchDialog({ problem, onLaunched }) {
  const cfg = state.config || await api.config();
  const { harnesses, errors } = await api.harnesses();
  const problemData = await api.problem(problem);

  if (!harnesses.length) {
    toast('No harnesses available' + (Object.keys(errors || {}).length
      ? `: ${Object.values(errors)[0]}` : ''), 'err');
    return;
  }

  const saved = JSON.parse(localStorage.getItem('atelier-launch') || '{}');
  const pick = (id) => harnesses.find(x => x.id === id);
  let harness = pick(saved.harness) || pick(cfg.defaults.harness) || harnesses[0];

  // ---- controls ---------------------------------------------------------
  const harnessSel = h('select', {},
    harnesses.map(x => h('option', { value: x.id, selected: x.id === harness.id },
      `${x.title}${x.runner === 'script' ? '  (scripted)' : ''}`)));
  const harnessNote = h('div', { class: 'small muted', style: { marginTop: '4px' } });
  const readerLevelLabel = h('span', { text: 'Reader level' });
  const readerLevelSel = h('select', {});
  const readerLevelNote = h('div', { class: 'small muted', style: { marginTop: '4px' } });
  const readerLevelField = h('label', { class: 'field', style: { display: 'none' } },
    readerLevelLabel, readerLevelSel, readerLevelNote);

  const modeNote = h('div', { class: 'small muted', style: { marginTop: '4px' } });
  const modeSeg = seg(['standard', 'oneshot'],
    saved.mode === 'oneshot' ? 'oneshot' : 'standard',
    ['Standard — sees progress, writes to the problem',
     'One-shot — blind control, writes nowhere']);

  const accountSel = h('select', {},
    (cfg.accounts || []).map(a => h('option', {
      value: a.id, selected: a.id === (saved.account || cfg.defaults.account),
    }, `${a.label} (${a.provider})`)));

  // The model list follows the account, because the account decides which
  // program is actually run. Offering a Claude model to Codex would fail at the
  // provider with an error nobody outside this codebase could interpret.
  const providerOf = (id) =>
    (cfg.accounts || []).find(a => a.id === id)?.provider || cfg.defaults.provider;
  const modelSel = h('select', {}, h('option', { value: '__custom' }, 'custom…'));
  const fillModels = (preferred) => {
    const prov = providerOf(accountSel.value);
    const list = (cfg.models_by_provider || {})[prov] || cfg.models || [];
    const want = preferred
      || ((cfg.default_model_by_provider || {})[prov] || cfg.defaults.model);
    clear(modelSel);
    list.forEach(m => modelSel.append(h('option', { value: m }, m)));
    modelSel.append(h('option', { value: '__custom' }, 'custom…'));
    modelSel.value = list.includes(want) ? want : (list[0] || '__custom');
  };
  const modelCustom = h('input', {
    type: 'text', placeholder: 'model id', style: { display: 'none', marginTop: '6px' },
  });
  accountSel.addEventListener('change', () => fillModels());
  fillModels(saved.model || null);
  modelSel.addEventListener('change', () => {
    modelCustom.style.display = modelSel.value === '__custom' ? '' : 'none';
  });

  const effortSeg = seg(cfg.efforts || ['low', 'medium', 'high', 'xhigh', 'max'], 'high');

  const taskSeg = seg(['default', 'text', 'files'], saved.task_kind || 'default',
    ['Use the harness default', 'Write a prompt', 'Point at files']);
  const taskText = h('textarea', {
    placeholder: 'What should this session do?', style: { minHeight: '90px' },
  });
  const taskFiles = h('div', { class: 'filelist', style: { maxHeight: '170px', overflowY: 'auto' } },
    (problemData.source_files || problemData.markdown_files || []).map(f => h('label', {
      class: 'filerow', style: { cursor: 'pointer' },
    },
      h('input', { type: 'checkbox', value: f, style: { marginRight: '6px' } }),
      h('span', { class: 'fname', text: f }))));
  const taskBox = h('div', {});

  const maxUsd = h('input', { type: 'number', step: '0.5', min: '0' });
  const maxMin = h('input', { type: 'number', step: '5', min: '0' });
  const maxTok = h('input', { type: 'number', step: '10000', min: '0' });

  const budgetNote = h('div', { class: 'small muted', style: { marginTop: '-6px' } });

  // Only a harness with a review loop has rounds, so the field appears only for
  // one. It is the honest place to spend less: each round is two more passes.
  const rounds = h('input', { type: 'number', step: '1', min: '0', max: '4' });
  const roundsNote = h('div', { class: 'small muted', style: { marginTop: '-6px' } });
  const roundsLabel = h('span', { text: 'Rounds of review' });
  const roundsField = h('label', { class: 'field', style: { display: 'none' } },
    roundsLabel, rounds, roundsNote);

  const budgets = h('div', {},
    h('div', { class: 'grid3' },
      h('label', { class: 'field' }, h('span', { text: 'Max $' }), maxUsd),
      h('label', { class: 'field' }, h('span', { text: 'Max minutes' }), maxMin),
      h('label', { class: 'field' }, h('span', { text: 'Max output tokens' }), maxTok)),
    budgetNote);

  const advanced = h('details', {},
    h('summary', { class: 'small muted', style: { cursor: 'pointer', margin: '4px 0 10px' } },
      'Advanced'),
    h('label', { class: 'field' },
      h('span', { text: 'Notes (recorded with the run)' }),
      h('input', { type: 'text', id: 'launch-notes' })));

  function applyHarness() {
    harness = pick(harnessSel.value) || harness;
    harnessNote.textContent = harness.description || '';
    paintReaderLevel();
    const d = harness.defaults || {};
    fillModels(d.model || null);
    setSeg(effortSeg, d.effort || cfg.defaults.effort);
    const b = harness.budget || {};
    maxUsd.value = b.max_usd ?? '';
    maxMin.value = b.max_minutes ?? '';
    maxTok.value = b.max_tokens_out ?? '';
    // Picking a tool-less method used to force one-shot, because in standard
    // mode it could not affect the problem at all. It can now — atelier folds
    // its answer into the document — so standard is a real choice and the note
    // below explains what each one does.
    paintModeNote();
    const loop = harness.loop;
    roundsField.style.display = loop ? '' : 'none';
    if (loop) {
      rounds.value = loop.rounds;
      const pipeline = loop.sequence?.length;
      rounds.max = pipeline ? '1' : '4';
      roundsLabel.textContent = pipeline ? 'Verification pipeline' : 'Rounds of review';
      roundsNote.textContent = pipeline
        ? `${loop.passes} independent passes: guide, graph, blind verification, `
          + `source verification, revision. Use 1 to run it or 0 for the guide only.`
        : `A draft, then each round reads it against the document and revises. `
          + `${loop.rounds} rounds is ${loop.passes} passes and the budget above is `
          + `divided between them. A round that raises nothing at `
          + `"${loop.continue_at}" or worse ends the loop early. 0 turns it off.`;
    }
    const pct = Math.round((b.wrapup_at ?? 0.8) * 100);
    budgetNote.textContent =
      `Defaults from the ${harness.title} harness. Wrap-up is triggered at ${pct}%; `
      + `leave a field empty for no cap.`;
    paintTask();
  }
  harnessSel.addEventListener('change', applyHarness);

  function paintReaderLevel() {
    const siblings = harness.family
      ? harnesses.filter(x => x.family === harness.family && x.variant)
      : [];
    readerLevelField.style.display = siblings.length > 1 ? '' : 'none';
    if (siblings.length <= 1) return;
    readerLevelLabel.textContent = harness.family === 'deslopify'
      ? 'Reader level'
      : 'Harness variant';
    clear(readerLevelSel);
    siblings.forEach(x => readerLevelSel.append(h('option', {
      value: x.id, selected: x.id === harness.id,
    }, x.variant_label || x.variant)));
    readerLevelSel.value = harness.id;
    readerLevelNote.textContent = harness.family === 'deslopify'
      ? 'Expert assumes field familiarity; Non-expert targets a PhD mathematician outside the field.'
      : 'Choose which version of this harness to run.';
  }

  readerLevelSel.addEventListener('change', () => {
    if (!pick(readerLevelSel.value)) return;
    harnessSel.value = readerLevelSel.value;
    applyHarness();
  });

  // A tool-less method has no file access, so atelier writes its answer into the
  // problem's own document on its behalf. That is an edit to the mathematical
  // record made by the app rather than by the session, so it is disclosed here
  // rather than discovered afterwards.
  function paintModeNote() {
    const toolless = !!(harness && harness.tools_none);
    const standard = getSeg(modeSeg) === 'standard';
    modeNote.textContent = !toolless ? ''
      : standard
        ? `${harness.title} has no file access, so atelier will add its answer to `
          + "this problem's document as a new, clearly marked section, and rebuild "
          + 'the PDF.'
        : `${harness.title} has no file access. This blind attempt keeps its own `
          + 'PDF with the session and does not touch the problem.';
  }
  modeSeg.addEventListener('click', () => setTimeout(paintModeNote, 0));

  function paintTask() {
    const kind = getSeg(taskSeg);
    mount(taskBox, kind === 'text' ? taskText : kind === 'files' ? taskFiles
      : h('div', { class: 'small muted', style: { padding: '8px 0' } },
        harness.default_task
          ? `“${harness.default_task}”`
          : 'The harness will choose from the current state.'));
  }
  taskSeg.addEventListener('click', () => setTimeout(paintTask, 0));

  // ---- preview ----------------------------------------------------------
  const previewBox = h('div', {});

  async function doPreview() {
    mount(previewBox, empty('Compiling…'));
    try {
      const p = await api.preview(harnessSel.value, buildBody());
      mount(previewBox,
        h('div', { class: 'small muted', style: { marginBottom: '8px' } },
          `${p.prompt_chars.toLocaleString()} characters ≈ ${p.prompt_tokens_estimate.toLocaleString()} `
          + `tokens · ${p.model} (${p.effort}) · cwd ${p.cwd}`),
        h('details', { open: true },
          h('summary', { class: 'small', style: { cursor: 'pointer' } }, 'Opening message'),
          h('pre', { class: 'raw', text: p.prompt })),
        h('details', {},
          h('summary', { class: 'small', style: { cursor: 'pointer' } }, 'System prompt'),
          h('pre', { class: 'raw', text: p.system_append })),
        h('details', {},
          h('summary', { class: 'small', style: { cursor: 'pointer' } }, 'Command line'),
          h('pre', { class: 'raw', text: p.argv_display })),
        h('details', {},
          h('summary', { class: 'small', style: { cursor: 'pointer' } }, 'Permissions & hooks'),
          h('pre', { class: 'raw', text: JSON.stringify(p.settings, null, 2) })));
    } catch (e) { mount(previewBox, empty(e.message)); }
  }

  function buildBody() {
    const kind = getSeg(taskSeg);
    const model = modelSel.value === '__custom' ? modelCustom.value.trim() : modelSel.value;
    return {
      problem,
      harness: harnessSel.value,
      mode: getSeg(modeSeg),
      account: accountSel.value || null,
      model: model || null,
      effort: getSeg(effortSeg),
      task_kind: kind,
      task_text: kind === 'text' ? taskText.value : '',
      task_files: kind === 'files'
        ? [...taskFiles.querySelectorAll('input:checked')].map(i => i.value) : [],
      max_usd: numOrNull(maxUsd.value),
      max_minutes: numOrNull(maxMin.value),
      max_tokens_out: numOrNull(maxTok.value),
      loop_rounds: harness.loop ? roundsOrNull(rounds.value) : null,
      notes: document.getElementById('launch-notes')?.value || '',
    };
  }

  // ---- dialog -----------------------------------------------------------
  const noStatement = !problemData.has_statement
    ? h('div', {
        class: 'card',
        style: { borderColor: 'var(--run)', marginBottom: '14px' },
      },
        h('div', { class: 'warn', style: { fontWeight: 600, marginBottom: '4px' } },
          `No statement file — ${problemData.meta.statement} does not exist`),
        h('div', { class: 'small muted' },
          'The session will only see whatever you type in Task below. Create '
          + `${problemData.meta.statement} in the problem folder if you want the `
          + 'statement carried into every future session automatically.'))
    : null;

  const dlg = h('dialog', { class: 'wide' },
    h('div', { class: 'dlg-head' },
      h('h2', { text: `New session · ${problemData.title}` })),
    h('div', { class: 'dlg-body' },
      noStatement,
      h('div', { class: 'grid2' },
        h('label', { class: 'field' }, h('span', { text: 'Harness' }), harnessSel, harnessNote),
        h('label', { class: 'field' }, h('span', { text: 'Mode' }), modeSeg, modeNote)),
      readerLevelField,
      h('div', { class: 'grid3' },
        h('label', { class: 'field' }, h('span', { text: 'Account' }), accountSel),
        h('label', { class: 'field' }, h('span', { text: 'Model' }), modelSel, modelCustom),
        h('label', { class: 'field' }, h('span', { text: 'Effort' }), effortSeg)),
      h('label', { class: 'field' }, h('span', { text: 'Task' }), taskSeg),
      taskBox,
      h('label', { class: 'field' },
        h('span', { text: 'Budget — whichever limit is reached first binds' }), budgets),
      roundsField,
      advanced,
      h('details', {},
        h('summary', {
          class: 'small', style: { cursor: 'pointer', margin: '10px 0' },
          onclick: () => setTimeout(doPreview, 0),
        }, 'Preview the exact prompt'),
        previewBox)),
    h('div', { class: 'dlg-foot' },
      h('span', { class: 'small muted', id: 'launch-hint', style: { marginRight: 'auto' } }),
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Cancel'),
      h('button', { class: 'btn primary', id: 'launch-go' }, 'Launch')));

  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  applyHarness();
  dlg.showModal();

  const hint = dlg.querySelector('#launch-hint');
  const updateHint = () => {
    const b = buildBody();
    const caps = [b.max_usd ? usd(b.max_usd) : null,
                  b.max_minutes ? `${b.max_minutes} min` : null].filter(Boolean);
    hint.textContent = caps.length ? `Capped at ${caps.join(' / ')}` : 'No budget cap set';
  };
  dlg.addEventListener('input', updateHint);
  dlg.addEventListener('click', () => setTimeout(updateHint, 0));
  updateHint();

  dlg.querySelector('#launch-go').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = 'Launching…';
    const body = buildBody();
    localStorage.setItem('atelier-launch', JSON.stringify({
      harness: body.harness, mode: body.mode, account: body.account,
      task_kind: body.task_kind,
    }));
    try {
      const r = await api.launch(body);
      dlg.close();
      toast('Session started', 'ok');
      onLaunched?.(r.run_id);
    } catch (err) {
      toast(err.message, 'err');
      btn.disabled = false;
      btn.textContent = 'Launch';
    }
  });
}

// -------------------------------------------------------------------- utils

function seg(values, initial, titles = []) {
  const el = h('div', { class: 'seg' }, values.map((v, i) => h('button', {
    type: 'button', text: v, 'data-v': v, title: titles[i] || '',
    class: v === initial ? 'on' : '',
    onclick: (e) => {
      [...el.children].forEach(b => b.classList.toggle('on', b === e.target));
    },
  })));
  return el;
}

function getSeg(el) {
  return el.querySelector('button.on')?.dataset.v ?? el.querySelector('button')?.dataset.v;
}

function setSeg(el, value) {
  [...el.children].forEach(b => b.classList.toggle('on', b.dataset.v === value));
  if (!el.querySelector('button.on')) el.children[0]?.classList.add('on');
}

function numOrNull(v) {
  const n = Number(v);
  return v === '' || Number.isNaN(n) || n <= 0 ? null : n;
}

// Rounds are not a budget. For a cap, 0 and empty both mean "no cap", which is
// what numOrNull encodes; for rounds, 0 means "no review at all" and is exactly
// the thing someone types when they want to spend less. Sending null there
// silently reinstated the harness default.
function roundsOrNull(v) {
  const n = Number(v);
  return v === '' || Number.isNaN(n) || n < 0 ? null : Math.floor(n);
}
