// Intake: paste what Claude produced, and bcn identifies, checks, places and validates it.
// The only place the UI writes content, and it writes nothing itself: bcn intake does.
import { api, awaitJob, h, mount, store } from '../core.js';

function diffView(text) {
  return h('div', { class: 'diff' }, text.split('\n').map(l => h('div', {
    class: l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '' }, l || ' ')));
}

export function intakeView(main) {
  const ui = { text: sessionStorage.getItem('intake-draft') || '', path: '.', busy: false, result: null, error: null };

  async function submit(dryRun) {
    ui.busy = true; ui.error = null; ui.result = null; render();
    try {
      const job = await api('/api/intake', { method: 'POST', body: { text: ui.text, dry_run: dryRun, path: ui.path } });
      store.jobs.set(job.id, job);
      const done = await awaitJob(job.id);
      ui.result = { env: done.envelopes?.[0], dryRun };
      if (!ui.result.env) ui.error = 'bcn intake produced no result; see the job log.';
      else if (!dryRun && ui.result.env.results.every(r => ['written', 'unchanged'].includes(r.action))) {
        sessionStorage.removeItem('intake-draft');
      }
    } catch (e) { ui.error = e.message; }
    ui.busy = false; render();
  }

  function results() {
    const env = ui.result?.env;
    if (!env) return null;
    const label = { written: 'written', unchanged: 'already identical', exists_differs: 'exists and differs: not written',
      refused: 'refused: not in course map', would_write: 'would be written' };
    return h('div', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, `${ui.result.dryRun ? 'Check' : 'Result'}: ${env.topics_found ?? env.results.length} topic${env.results.length === 1 ? '' : 's'} found`)),
      env.results.map(r => {
        const errs = (env.diagnostics || []).filter(d => d.topic === r.topic && !d.code.startsWith('INTAKE_'));
        return h('div', { class: 'job' },
          h('div', { class: 'top' },
            h('span', { class: `chip ${['written', 'unchanged', 'would_write'].includes(r.action) ? 'ok' : 'error'}` }, label[r.action] || r.action || 'failed'),
            r.path ? h('a', { href: `#/topic/${r.topic}` }, r.topic) : h('strong', {}, r.topic),
            r.validate_ok !== undefined ? h('span', { class: `chip ${r.validate_ok ? 'ok' : 'error'}` }, r.validate_ok ? 'validates' : 'validation errors') : null),
          errs.length ? h('ul', { class: 'diaglist' }, errs.map(d => h('li', {},
            h('div', {}, h('span', { class: `chip ${d.level}` }, d.level)),
            h('div', {}, d.message, h('div', { class: 'loc' }, `${d.code}${d.line ? ' · line ' + d.line : ''}${d.slide ? ' · slide ' + d.slide : ''}`),
              d.hint ? h('div', { class: 'hint' }, d.hint) : null)))) : null,
          r.diff ? diffView(r.diff) : null);
      }));
  }

  function render() {
    const modules = Object.keys(store.status?.summary?.modules || {}).sort();
    const ta = h('textarea', { rows: 22, placeholder: 'Paste one topic or a whole unit, front matter included. Code fences are fine.',
      oninput: (e) => {
        ui.text = e.target.value;
        sessionStorage.setItem('intake-draft', ui.text);
        main.querySelectorAll('button[data-needs-text]').forEach(b => { b.disabled = ui.busy || !ui.text.trim(); });
      } }, ui.text);
    mount(main,
      h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Intake'),
        h('div', { class: 'sub' }, 'Paste, and bcn identifies each topic by its front matter, checks it against the course map, places it, and validates it. It never overwrites a file that differs; it shows the diff instead.'))),
      h('div', { class: 'grid-2' },
        h('div', { class: 'panel' }, h('div', { class: 'panel-body stack' }, ta,
          h('div', { class: 'row' },
            h('span', { class: 'muted small' }, 'Restrict to'),
            h('select', { onchange: e => { ui.path = e.target.value; } },
              h('option', { value: '.' }, 'any module'), modules.map(m => h('option', { value: m, selected: ui.path === m }, m))),
            h('span', { class: 'spacer' }),
            h('button', { class: 'btn', 'data-needs-text': true, disabled: ui.busy || !ui.text.trim(), onclick: () => submit(true) }, 'Check only'),
            h('button', { class: 'btn primary', 'data-needs-text': true, disabled: ui.busy || !ui.text.trim(), onclick: () => submit(false) }, ui.busy ? 'Working…' : 'Place and validate')))),
        h('div', {}, ui.error ? h('div', { class: 'banner error', style: { margin: '0 0 12px' } }, ui.error) : null,
          results() || h('div', { class: 'panel empty' }, 'Results appear here.'))));
  }
  render();
  return null;
}
