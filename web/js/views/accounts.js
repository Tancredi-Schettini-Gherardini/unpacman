// Accounts: four independent credit signals, kept visibly separate — plus
// environment checks and spend breakdowns.

import { api } from '../api.js';
import { state } from '../app.js';
import {
  ago, card, dur, empty, h, int, meter, mount, stat, table, toast, usd,
} from '../util.js';

export async function render(root) {
  const [accounts, doctor, byProblem, byHarness, byDay] = await Promise.all([
    api.accounts(),
    api.doctor(false),
    api.usage('problem'), api.usage('harness'), api.usage('day', 30),
  ]);

  const cards = accounts.map(a => accountCard(a));

  const checks = (doctor.checks || []).filter(c => c.level !== 'ok');
  const doctorCard = card(
    checks.length ? `Environment — ${checks.length} thing${checks.length === 1 ? '' : 's'} to look at`
                  : 'Environment — all good',
    h('div', { class: 'small' }, (checks.length ? checks : doctor.checks).map(c => h('div', {
      style: { display: 'flex', gap: '10px', padding: '3px 0' },
    },
      h('span', { class: c.level === 'fail' ? 'err' : c.level === 'warn' ? 'warn' : 'ok' },
        c.level === 'fail' ? '✗' : c.level === 'warn' ? '!' : '✓'),
      h('span', { style: { width: '170px', flex: '0 0 170px' }, text: c.name }),
      h('span', { class: 'muted', style: { flex: 1 } },
        c.detail + (c.fix && c.level !== 'ok' ? `  →  ${c.fix}` : ''))))),
    h('div', { style: { marginTop: '10px' } },
      h('button', {
        class: 'btn sm',
        onclick: async (e) => {
          e.target.disabled = true; e.target.textContent = 'Probing…';
          try {
            const live = await api.doctor(true);
            toast(`Checked: ${live.worst}`, live.worst === 'fail' ? 'err' : 'ok');
            render(root);
          } catch (err) { toast(err.message, 'err'); }
        },
      }, 'Run live checks')));

  mount(root,
    h('div', { class: 'page-head' },
      h('div', {},
        h('h1', { text: 'Accounts & spend' }),
        h('div', { class: 'page-sub' },
          'Measured spend is exact. Window utilisation is against caps you set. '
          + 'Provider status is whatever the CLI volunteered.'))),
    h('div', { class: 'pad' },
      cards.length ? cards : empty('No accounts configured. Add one to accounts.toml.'),
      doctorCard,
      h('div', { class: 'grid2' },
        usageCard('Spend by problem', byProblem.rows),
        usageCard('Spend by harness', byHarness.rows)),
      usageCard('Spend by day (30d)', byDay.rows, true)));
}

function accountCard(a) {
  const windows = a.windows || [];
  const rows = windows.map(w => h('div', { style: { marginBottom: '10px' } },
    h('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      h('span', { class: 'small dim' },
        `last ${w.label} · ${w.runs} session${w.runs === 1 ? '' : 's'}`),
      h('span', { class: 'small num' },
        w.cap ? `${usd(w.spend)} / ${usd(w.cap)}` : usd(w.spend))),
    w.cap ? meter(w.fraction)
          : h('div', { class: 'small muted', text: 'no cap set — add soft_cap to accounts.toml' })));

  const provider = a.provider_window;
  const providerRow = provider
    ? h('div', { class: 'small' },
        h('strong', 'Provider reports: '),
        h('span', { class: provider.status === 'allowed' ? 'ok' : 'warn' },
          `${provider.status}`),
        h('span', { class: 'muted' },
          ` · ${provider.window || 'window'}`
          + (provider.resets_at ? ` · resets ${new Date(provider.resets_at).toLocaleString()}` : '')
          + ` · seen ${ago(provider.at)}`))
    : h('div', { class: 'small muted' },
        'Provider has not volunteered window state yet — it arrives during a session.');

  const limited = a.limited
    ? h('div', { class: 'chip bad', style: { marginTop: '8px' } },
        `Rate limited${a.limited_until
          ? ` until ${new Date(a.limited_until).toLocaleString()}` : ''}`)
    : null;

  return card(null,
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' } },
      h('span', { class: 'dot', style: { background: a.color, width: '10px', height: '10px' } }),
      h('div', { style: { flex: 1 } },
        h('div', { style: { fontWeight: 600 } }, a.label),
        h('div', { class: 'small muted' },
          `${a.provider} · ${a.auth}${a.plan && a.plan !== 'unknown' ? ` · ${a.plan}` : ''}`)),
      h('button', {
        class: 'btn sm',
        onclick: async (e) => {
          e.target.disabled = true;
          try {
            const r = await api.probeAccount(a.id);
            toast(`${a.id}: ${r.status}`, 'ok');
          } catch (err) { toast(err.message, 'err'); }
          finally { e.target.disabled = false; }
        },
      }, 'Probe')),
    rows,
    providerRow,
    a.last_probe ? h('div', { class: 'small muted', style: { marginTop: '6px' } },
      `CLI auth: ${a.last_probe} (${ago(a.last_probe_at)})`) : null,
    limited);
}

function usageCard(title, rows, isDay = false) {
  if (!rows || !rows.length) return card(title, empty('No data yet.'));
  const max = Math.max(...rows.map(r => r.cost || 0), 0.0001);
  return card(title,
    h('div', { class: 'small' }, rows.slice(0, 18).map(r => h('div', {
      style: { display: 'flex', alignItems: 'center', gap: '10px', padding: '3px 0' },
    },
      h('span', {
        style: { width: isDay ? '86px' : '150px', flex: `0 0 ${isDay ? 86 : 150}px`,
                 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
        text: r.key || '—',
      }),
      h('div', { class: 'meter', style: { flex: 1, marginTop: 0 } },
        h('i', { style: { width: `${(r.cost / max) * 100}%` } })),
      h('span', { class: 'num muted', style: { width: '64px', textAlign: 'right' } },
        usd(r.cost)),
      h('span', { class: 'num muted', style: { width: '34px', textAlign: 'right' } },
        int(r.runs))))));
}
