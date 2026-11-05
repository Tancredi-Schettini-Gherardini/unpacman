// Harnesses: the method library, with the rendered SKILL.md and live stats.

import { api } from '../api.js';
import { navigate } from '../app.js';
import {
  card, clear, dur, empty, h, int, mount, stat, table, toast, typeset, usd,
} from '../util.js';

export async function render(root, route) {
  const { harnesses, errors } = await api.harnesses();

  const problems = await api.problems();

  const errNodes = Object.entries(errors || {}).map(([name, msg]) =>
    h('div', { class: 'card', style: { borderColor: 'var(--err)' } },
      h('h3', { class: 'err', text: `${name} — failed to load` }),
      h('div', { class: 'small mono', text: msg })));

  const rows = harnesses.map(x => h('tr', {},
    h('td', {}, h('a', {
      href: `/harnesses/${x.id}`,
      onclick: (e) => { e.preventDefault(); openHarness(x.id, problems); },
    }, x.title)),
    h('td', { class: 'mono small dim' }, `${x.id} v${x.version}`),
    h('td', { class: 'small dim' }, x.runner + (x.tools_none ? ' · no tools' : '')),
    h('td', { class: 'small muted' }, x.description || ''),
    h('td', { class: 'num small' }, int(x.stats.runs)),
    h('td', { class: 'num small' }, x.stats.runs ? usd(x.stats.avg_cost) : '—'),
    h('td', { class: 'num small' }, x.stats.runs ? dur(x.stats.avg_seconds) : '—'),
    h('td', { class: 'small' }, x.warnings?.length
      ? h('span', { class: 'warn', text: `${x.warnings.length} warning` }) : '')));

  mount(root,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { text: 'Harnesses' }),
        h('div', { class: 'page-sub' },
          'Methods, versioned and reusable across problems. '
          + 'Edit them under harnesses/ — changes are picked up on the next launch.'))),
    h('div', { class: 'pad' },
      errNodes,
      table([{ label: 'Harness' }, { label: 'ID' }, { label: 'Runner' },
             { label: 'Description' }, { label: 'Runs', num: true },
             { label: 'Median cost', num: true }, { label: 'Median time', num: true },
             { label: '' }], rows)));

  if (route.slug) openHarness(route.slug, problems);
}

async function openHarness(id, problems) {
  const box = h('div', { class: 'dlg-body' }, empty('Loading…'));
  const dlg = h('dialog', { class: 'wide' },
    h('div', { class: 'dlg-head' }, h('h2', { text: id })),
    box,
    h('div', { class: 'dlg-foot' },
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')));
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.showModal();

  try {
    const x = await api.harness(id);
    const budgetBits = [
      x.budget.max_usd ? usd(x.budget.max_usd) : null,
      x.budget.max_minutes ? `${x.budget.max_minutes} min` : null,
      x.budget.max_tokens_out ? `${int(x.budget.max_tokens_out)} out tok` : null,
    ].filter(Boolean);

    const validateSel = h('select', {},
      problems.map(p => h('option', { value: p.slug }, p.title)));
    const validateOut = h('div', { style: { marginTop: '10px' } });

    mount(box,
      h('div', { class: 'stats', style: { marginBottom: '14px' } },
        stat('Version', `v${x.version}`),
        stat('Runner', x.runner),
        stat('Effort', x.defaults.effort || '—'),
        stat('Model', x.defaults.model || 'session default'),
        stat('Budget', budgetBits.join(' / ') || 'none'),
        stat('Wrap-up at', `${Math.round(x.budget.wrapup_at * 100)}%`)),

      x.warnings?.length ? card('Warnings',
        h('ul', { class: 'small warn', style: { paddingLeft: '18px' } },
          x.warnings.map(w => h('li', { text: w })))) : null,

      card('Method (SKILL.md)', (() => {
        const el = h('div', { class: 'prose wide', html: x.skill_html || '<p class="muted">none</p>' });
        setTimeout(() => typeset(el), 0);
        return el;
      })()),

      x.agents.length ? card('Subagents',
        x.agents.map(a => h('details', { style: { margin: '6px 0' } },
          h('summary', {}, h('strong', { text: a.name }),
            h('span', { class: 'muted small', text: `  ${a.description}` })),
          h('pre', { class: 'raw', text: a.prompt })))) : null,

      card('Tools',
        x.tools.none
          ? h('div', { class: 'small muted', text: 'No tools at all — this is a control condition.' })
          : h('div', { class: 'small' },
            h('div', {}, h('strong', 'allow: '), (x.tools.allow || []).join(', ') || '(default set)'),
            x.tools.deny?.length ? h('div', {}, h('strong', 'deny: '), x.tools.deny.join(', ')) : null,
            x.tools.bash_allow?.length
              ? h('div', { class: 'mono', style: { marginTop: '6px' } },
                'bash allow: ' + x.tools.bash_allow.join('  ')) : null,
            x.tools.bash_deny?.length
              ? h('div', { class: 'mono err' }, 'bash deny: ' + x.tools.bash_deny.join('  ')) : null)),

      card('Context sent to the model',
        h('div', { class: 'small' },
          Object.entries(x.context).map(([k, v]) => h('div', {
            style: { display: 'flex', justifyContent: 'space-between' },
          }, h('span', { text: k.replace(/_/g, ' ') }),
             h('span', { class: 'muted', text: String(v) }))))),

      card('Dry run',
        h('div', { class: 'small muted', style: { marginBottom: '8px' } },
          'Compile this harness against a problem and inspect exactly what would be sent. '
          + 'Nothing is launched and nothing is spent.'),
        h('div', { style: { display: 'flex', gap: '8px' } },
          validateSel,
          h('button', {
            class: 'btn sm',
            onclick: async () => {
              mount(validateOut, empty('Compiling…'));
              try {
                const p = await api.preview(id, { problem: validateSel.value, harness: id });
                mount(validateOut,
                  h('div', { class: 'small muted' },
                    `${p.prompt_chars.toLocaleString()} chars ≈ `
                    + `${p.prompt_tokens_estimate.toLocaleString()} tokens`),
                  h('details', { open: true },
                    h('summary', { class: 'small' }, 'Opening message'),
                    h('pre', { class: 'raw', text: p.prompt })),
                  h('details', {},
                    h('summary', { class: 'small' }, 'Command line'),
                    h('pre', { class: 'raw', text: p.argv_display })));
              } catch (e) { mount(validateOut, empty(e.message)); }
            },
          }, 'Compile')),
        validateOut));
  } catch (e) {
    mount(box, empty(e.message));
  }
}
