// Module: units down, topics across, one split cell per topic (English | Mandarin).
// Stage is shown by position; colour marks only stale and blocked. Bulk actions live here,
// where the scope is visible, and say what they will do before they run.
import { confirmModal, h, mount, pips, runJob, STAGE_LABEL, STEP_HELP, store, subscribe, topicPath } from '../core.js';

const BULK = ['validate', 'render', 'script', 'bumpers', 'cues', 'compose', 'package'];

export function moduleView(main, module) {
  const state = {
    selected: new Set(),
    lang: 'en',             // language for bulk actions
    force: false,
    filter: { stage: '', lang: 'en', stale: false, blocked: false },
  };

  const matches = (r) => {
    const f = state.filter;
    const sides = f.lang === 'both' ? [r.en, r.zh] : [r[f.lang]];
    if (f.stage && !sides.some(s => s.stage === f.stage)) return false;
    if (f.stale && !sides.some(s => s.stale)) return false;
    if (f.blocked && !sides.some(s => s.blocked)) return false;
    return true;
  };

  function half(s, lang) {
    const cls = ['half', s.stale ? 'stale' : '', s.blocked ? 'blocked' : ''].join(' ');
    const tip = [`${lang.toUpperCase()}: ${STAGE_LABEL[s.stage]}`, s.stale ? `stale (${s.stale_steps.join(', ')})` : '',
      s.blocked ? `blocked: ${s.blockers.map(b => b.code).join(', ')}` : '', s.next ? `next: ${s.next}` : ''].filter(Boolean).join('\n');
    return h('div', { class: cls, title: tip },
      pips(s.stage_index, s.stages.length),
      h('span', { class: 'lbl' }, STAGE_LABEL[s.stage]));
  }

  function cell(r) {
    const cloud = r.hydration === 'cloud' || r.hydration === 'partial';
    const planned = r.en.stage === 'planned';
    const cls = ['cell', cloud ? 'cloud' : '', planned ? 'planned' : '', state.selected.has(r.topic) ? 'selected' : '',
      matches(r) ? '' : 'dim'].join(' ');
    const sel = h('input', { type: 'checkbox', class: 'sel', 'aria-label': `Select ${r.topic}`, checked: state.selected.has(r.topic),
      onclick: (e) => { e.stopPropagation(); toggle(r.topic, e.target.checked); } });
    const cols = store.boot.prefs.module_columns || { en: true, zh: true };
    return h('a', { class: cls, href: `#/topic/${r.topic}`, title: `${r.topic}${r.title ? ' — ' + r.title : ''}${cloud ? '\ncloud-only files' : ''}`,
      onclick: (e) => { if (e.metaKey || e.shiftKey || e.ctrlKey) { e.preventDefault(); toggle(r.topic, !state.selected.has(r.topic)); } } },
      h('span', { class: 'code' }, `${r.code}${cloud ? ' ☁' : ''}`), planned ? null : sel,
      cols.en !== false ? half(r.en, 'en') : null, cols.zh !== false ? half(r.zh, 'zh') : null,
      r.unreviewed_mistranscriptions ? h('span', { class: 'unrev', title: 'suspected mis-transcriptions to review' }, `✎${r.unreviewed_mistranscriptions}`) : null);
  }

  function toggle(id, on) {
    if (on) state.selected.add(id); else state.selected.delete(id);
    render();
  }

  // Collapse a selection to the widest directories it fully covers, so bcn can run them with --jobs.
  function targetsFor(ids, rows) {
    const onDisk = rows.filter(r => r.has_dir);
    const byUnit = new Map();
    for (const r of onDisk) { if (!byUnit.has(r.unit)) byUnit.set(r.unit, []); byUnit.get(r.unit).push(r.topic); }
    const units = [...byUnit.entries()];
    if (units.every(([, ts]) => ts.every(t => ids.has(t))) && ids.size === onDisk.length) return [module];
    const out = [];
    for (const [u, ts] of units) {
      const picked = ts.filter(t => ids.has(t));
      if (!picked.length) continue;
      if (picked.length === ts.length) out.push(`${module}/${u}`);
      else out.push(...picked.map(topicPath));
    }
    return out;
  }

  async function bulk(command, rows) {
    const ids = new Set([...state.selected].filter(id => rows.find(r => r.topic === id && r.has_dir)));
    if (!ids.size) return;
    const targets = targetsFor(ids, rows);
    const langName = state.lang === 'zh' ? 'Mandarin' : 'English';
    const lang = ['cues', 'script'].includes(command) ? 'en' : state.lang;  // English only
    const body = h('div', {},
      h('p', {}, `This runs `, h('strong', {}, `bcn ${command}${lang === 'zh' ? ' --lang zh' : ''}${state.force ? ' --force' : ''}`),
        ` on ${ids.size} topic${ids.size === 1 ? '' : 's'} (${command === 'cues' ? 'English; Mandarin inherits cues' : langName}), as ${targets.length} invocation${targets.length === 1 ? '' : 's'}:`),
      h('p', { class: 'mono small' }, targets.join('  ')),
      state.force ? h('p', {}, 'Force rebuilds topics whose outputs are already current.') :
        h('p', {}, 'Topics whose outputs are already current are skipped.'));
    if (!await confirmModal(`Run ${command} on ${ids.size} topic${ids.size === 1 ? '' : 's'}?`, body)) return;
    const args = { lang };
    if (state.force) args.force = true;
    await runJob(command, targets, args);
  }

  function docsPanel(m) {
    const docs = m.documents || [];
    if (!docs.length) return null;
    const shown = docs.filter(d => d.exists || d.path.endsWith('course-map.md') || d.path.endsWith('/activity.md'));
    return h('div', { class: 'panel', style: { marginBottom: '16px' } },
      h('div', { class: 'panel-head' }, h('h2', {}, 'Module documents'),
        m.errors ? h('span', { class: 'chip error' }, `${m.errors} error${m.errors === 1 ? '' : 's'}`) : h('span', { class: 'chip ok' }, 'no problems')),
      h('div', { class: 'actions docs-list' }, shown.map(d => d.exists
        ? h('a', { class: 'btn small', href: `#/doc/${d.path}` }, d.path.slice(module.length + 1),
            d.errors ? h('span', { class: 'chip error' }, d.errors) : d.warnings ? h('span', { class: 'chip warn' }, d.warnings) : null)
        : h('span', { class: 'btn small missing', title: 'Not in the working copy yet' }, `${d.path.slice(module.length + 1)} — missing`))));
  }

  const render = () => {
    const env = store.status;
    if (!env) { mount(main, h('div', { class: 'empty' }, 'Reading the programme…')); return; }
    const rows = env.results.filter(r => r.module === module);
    const m = env.summary.modules[module];
    if (!m) { mount(main, h('div', { class: 'empty' }, `No module ${module}.`)); return; }
    const units = [...new Set(rows.map(r => r.unit))].sort();
    const codes = [...new Set(rows.map(r => r.code))].sort();
    const unitTitle = m.unit_titles || {};
    const visible = rows.filter(matches);
    const stages = [...(rows[0]?.en.stages || []), ...(rows[0]?.zh.stages || [])];

    const f = state.filter;
    const filters = h('div', { class: 'filters' },
      h('strong', { class: 'small' }, 'Show'),
      h('select', { 'aria-label': 'Stage', onchange: e => { f.stage = e.target.value; render(); } },
        h('option', { value: '' }, 'any stage'),
        [...new Set(stages)].map(s => h('option', { value: s, selected: f.stage === s }, STAGE_LABEL[s]))),
      h('select', { 'aria-label': 'Language', onchange: e => { f.lang = e.target.value; render(); } },
        ['en', 'zh', 'both'].map(l => h('option', { value: l, selected: f.lang === l }, { en: 'English', zh: 'Mandarin', both: 'either language' }[l]))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.stale, onchange: e => { f.stale = e.target.checked; render(); } }), 'stale'),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.blocked, onchange: e => { f.blocked = e.target.checked; render(); } }), 'blocked'),
      h('span', { class: 'muted small' }, `${visible.length} of ${rows.length} match`),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn small', onclick: () => { visible.filter(r => r.has_dir).forEach(r => state.selected.add(r.topic)); render(); } }, 'Select matching'),
      h('button', { class: 'btn small', onclick: () => { state.selected.clear(); render(); } }, 'Clear selection'));

    const n = [...state.selected].filter(id => rows.some(r => r.topic === id)).length;
    const bulkbar = h('div', { class: 'bulkbar' },
      h('strong', {}, n ? `${n} selected` : 'Select topics to act on them'),
      h('select', { 'aria-label': 'Language for actions', disabled: !n, onchange: e => { state.lang = e.target.value; } },
        h('option', { value: 'en', selected: state.lang === 'en' }, 'English'), h('option', { value: 'zh', selected: state.lang === 'zh' }, 'Mandarin')),
      BULK.map(c => h('span', { 'data-tip': `${STEP_HELP[c]} Runs on the selected topics; you confirm first.` },
        h('button', { class: 'btn', disabled: !n, onclick: () => bulk(c, rows) }, c))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: state.force, disabled: !n, onchange: e => { state.force = e.target.checked; } }), 'force'),
      h('span', { class: 'spacer' }),
      h('span', { class: 'muted small' }, 'Click a cell to open it; ⌘-click or tick to select.'));

    const table = h('table', { class: 'topics' },
      h('thead', {}, h('tr', {}, h('th', {}), codes.map(c => h('th', {}, c)))),
      h('tbody', {}, units.map(u => h('tr', {},
        h('th', { class: 'unit' }, u, h('span', { class: 't' }, unitTitle[u] || '')),
        codes.map(c => { const r = rows.find(x => x.unit === u && x.code === c); return h('td', {}, r ? cell(r) : null); })))));

    mount(main,
      h('div', { class: 'page-head' },
        h('div', {}, h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Programme'), ' / ', module),
          h('h1', {}, `${module}${m.title ? ' · ' + m.title : ''}`),
          h('div', { class: 'sub' }, `${m.topics} topics · English ${m.complete.en} complete · Mandarin ${m.complete.zh} complete`)),
        h('div', { class: 'row' },
          m.blocked ? h('span', { class: 'chip blocked' }, `${m.blocked} blocked`) : null,
          m.stale ? h('span', { class: 'chip stale' }, `${m.stale} stale`) : null,
          m.cloud ? h('span', { class: 'chip cloud' }, `☁ ${m.cloud} cloud-only`) : null,
          h('button', { class: 'btn', onclick: () => runJob('qa', [module], {}) }, 'Run QA on module'))),
      docsPanel(m),
      h('div', { class: 'panel' }, filters, bulkbar, h('div', { class: 'gridwrap panel-body' }, table)),
      h('p', { class: 'muted small' },
        'Each cell: English on the left, Mandarin on the right. Filled pips show how far the topic has got. ',
        h('span', { class: 'chip stale' }, 'amber'), ' is stale (built from older inputs); ',
        h('span', { class: 'chip blocked' }, 'red'), ' is blocked (needs a person); a dashed cell has cloud-only files.'));
  };
  render();
  const off = subscribe(kind => { if (kind === 'status') render(); });
  return { dispose: off };
}
