// Settings: UI preferences only. Pipeline configuration lives in programme.toml.
import { api, h, mount, store, toast } from '../core.js';

export function settingsView(main) {
  const p = { ...store.boot.prefs };
  const save = async () => {
    try { store.boot.prefs = await api('/api/prefs', { method: 'PUT', body: p }); toast('Saved'); } catch (e) { toast(e.message, true); }
  };
  const field = (label, input, help) => h('div', { class: 'row', style: { marginBottom: '12px', alignItems: 'baseline' } },
    h('label', { style: { width: '220px', fontWeight: 600 } }, label), input, help ? h('span', { class: 'muted small' }, help) : null);
  const doctor = store.boot.doctor;
  mount(main,
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Settings'), h('div', { class: 'sub' }, `Programme root: ${store.boot.root}`))),
    h('div', { class: 'grid-2' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Preferences')), h('div', { class: 'panel-body' },
        field('Your name', h('input', { type: 'text', value: p.operator, oninput: e => { p.operator = e.target.value; } }), 'recorded against jobs and review decisions'),
        field('Topics in parallel', h('input', { type: 'number', min: 1, max: 16, value: p.jobs, oninput: e => { p.jobs = Number(e.target.value); } }), 'passed to bcn --jobs'),
        field('Jobs at once', h('input', { type: 'number', min: 1, max: 8, value: p.parallel_jobs, oninput: e => { p.parallel_jobs = Number(e.target.value); } }), 'never two on the same topic'),
        field('Status poll (s)', h('input', { type: 'number', min: 5, max: 600, value: p.poll_seconds, oninput: e => { p.poll_seconds = Number(e.target.value); } }), 'file changes also refresh at once'),
        field('Topic layout', h('select', { onchange: e => { p.topic_layout = e.target.value; } },
          ['side', 'stacked'].map(v => h('option', { value: v, selected: p.topic_layout === v }, v === 'side' ? 'side by side' : 'stacked')))),
        field('Module grid shows', h('span', { class: 'row' },
          ['en', 'zh'].map(l => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: p.module_columns?.[l] !== false,
            onchange: e => { p.module_columns = { ...(p.module_columns || {}), [l]: e.target.checked }; } }), l === 'en' ? 'English' : 'Mandarin')))),
        h('button', { class: 'btn primary', onclick: save }, 'Save'))),
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Tools (bcn doctor)')),
        doctor ? h('table', { class: 'data' }, h('thead', {}, h('tr', {}, ['Tool', 'Pinned', 'Found', ''].map(x => h('th', {}, x)))),
          h('tbody', {}, doctor.results.map(r => h('tr', {}, h('td', {}, r.topic), h('td', { class: 'mono' }, r.pinned), h('td', { class: 'small' }, r.found || '—'),
            h('td', {}, h('span', { class: `chip ${r.ok ? 'ok' : 'error'}` }, r.ok ? 'ok' : 'problem')))))) : h('div', { class: 'empty' }, 'Checking…'),
        h('p', { class: 'muted small', style: { padding: '0 14px' } }, 'Single user, bound to localhost, no authentication. See the README before exposing it anywhere else.'))));
  return null;
}
