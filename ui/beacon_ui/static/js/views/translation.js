// Translation round trip: a batch handover out, and a batch check on the way back,
// so everything wrong goes back to the translator in one message.
import { api, awaitJob, fmtAgo, fmtBytes, h, mount, runJob, store, toast } from '../core.js';

export function translationView(main) {
  const ui = { scope: '', lists: { exports: [], returned: [] }, lastImport: null, busy: false };

  async function refresh() {
    try { ui.lists = await api('/api/translation'); } catch (e) { toast(e.message, true); }
    render();
  }

  async function doExport() {
    if (!ui.scope) return;
    ui.busy = true; render();
    const job = await runJob('translation', [ui.scope], { export: true });
    const done = await awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env && !env.ok) toast((env.diagnostics || []).filter(d => d.level === 'error').map(d => d.message).join(' ') || 'Export failed.', true);
    ui.busy = false; refresh();
  }

  async function upload(file) {
    if (!file) return;
    try {
      const buf = await file.arrayBuffer();
      await api(`/api/translation/upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: buf, raw: true });
      toast(`Uploaded ${file.name}`);
    } catch (e) { toast(e.message, true); }
    refresh();
  }

  async function doImport(item) {
    ui.busy = true; render();
    try {
      const job = await api('/api/translation/import', { method: 'POST', body: { source: item.path } });
      store.jobs.set(job.id, job);
      const done = await awaitJob(job.id);
      ui.lastImport = { item, env: done.envelopes?.[0] };
    } catch (e) { toast(e.message, true); }
    ui.busy = false; render();
  }

  function message(env) {
    const items = env.return_to_translator || [];
    if (!items.length) return '';
    const byTopic = new Map();
    for (const x of items) { if (!byTopic.has(x.topic)) byTopic.set(x.topic, []); byTopic.get(x.topic).push(x.message); }
    return ['The following returned files need correcting. Slide breaks must match the English exactly, and SRT timings must be identical cue for cue.', '',
      ...[...byTopic.entries()].flatMap(([t, ms]) => [`${t}:`, ...ms.map(m => `  - ${m}`), ''])].join('\n');
  }

  function importResult() {
    const r = ui.lastImport;
    if (!r?.env) return null;
    const msg = message(r.env);
    return h('div', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, `Imported ${r.item.name}`),
        h('span', { class: `chip ${r.env.ok ? 'ok' : 'error'}` }, r.env.ok ? 'all passed' : 'problems found')),
      h('table', { class: 'data' }, h('thead', {}, h('tr', {}, ['Topic', 'Files', 'Parity', 'SRT timings'].map(x => h('th', {}, x)))),
        h('tbody', {}, r.env.results.map(x => h('tr', {},
          h('td', {}, h('a', { href: `#/topic/${x.topic}` }, x.topic)),
          h('td', { class: 'small' }, Object.entries(x.files || {}).map(([k, v]) => `${k}: ${v}`).join(', ')),
          h('td', {}, x.validate_ok === undefined ? '—' : h('span', { class: `chip ${x.validate_ok ? 'ok' : 'error'}` }, x.validate_ok ? 'ok' : 'fail')),
          h('td', {}, x.subtitles_ok === undefined ? '—' : h('span', { class: `chip ${x.subtitles_ok ? 'ok' : 'error'}` }, x.subtitles_ok ? 'ok' : 'fail')))))),
      msg ? h('div', { class: 'panel-body stack' },
        h('h3', {}, 'Back to the translator, in one message'),
        h('textarea', { rows: 10, readonly: true }, msg),
        h('button', { class: 'btn', onclick: () => navigator.clipboard.writeText(msg).then(() => toast('Copied')) }, 'Copy message')) : null);
  }

  function render() {
    const s = store.status?.summary?.modules || {};
    const scopes = Object.entries(s).sort().flatMap(([m, info]) => [m, ...info.units.map(u => `${m}/${u}`)]);
    const ready = (store.status?.results || []).filter(r => r.zh.next === 'translation_export' && (!ui.scope || r.path.startsWith(ui.scope)));
    mount(main,
      h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Translation'),
        h('div', { class: 'sub' }, 'Export sends the reviewed English SRT and narration-stripped slides. Import places the returned files and checks parity and timings across the batch.'))),
      h('div', { class: 'grid-2' },
        h('div', { class: 'stack' },
          h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Export')),
            h('div', { class: 'panel-body stack' },
              h('div', { class: 'row' },
                h('select', { onchange: e => { ui.scope = e.target.value; render(); } }, h('option', { value: '' }, 'choose a module or unit'),
                  scopes.map(x => h('option', { value: x, selected: ui.scope === x }, x))),
                h('button', { class: 'btn primary', disabled: !ui.scope || ui.busy, onclick: doExport }, 'Export batch')),
              h('p', { class: 'muted small' }, `${ready.length} topic${ready.length === 1 ? '' : 's'} in ${ui.scope || 'the programme'} ready to send. `,
                'A batch goes all or nothing: every topic in scope must have passed validate and subtitles, with its mis-transcriptions reviewed.'))),
          h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Exports')),
            ui.lists.exports.filter(x => x.kind === 'zip').length ? h('table', { class: 'data' }, h('tbody', {},
              ui.lists.exports.filter(x => x.kind === 'zip').map(x => h('tr', {},
                h('td', { class: 'mono' }, x.name), h('td', { class: 'small' }, fmtAgo(x.mtime)), h('td', { class: 'small num' }, fmtBytes(x.bytes)),
                h('td', {}, h('a', { class: 'btn small', href: `/files/${x.path}?download=1` }, 'Download'))))))
              : h('div', { class: 'empty' }, 'No exports yet.'))),
        h('div', { class: 'stack' },
          h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Import')),
            h('div', { class: 'panel-body stack' },
              (() => {
                const input = h('input', { type: 'file', accept: '.zip', class: 'hidden', onchange: e => upload(e.target.files[0]) });
                const zone = h('div', { class: 'dropzone', onclick: () => input.click(),
                  ondragover: e => { e.preventDefault(); zone.classList.add('over'); },
                  ondragleave: () => zone.classList.remove('over'),
                  ondrop: e => { e.preventDefault(); zone.classList.remove('over'); upload(e.dataTransfer.files[0]); } },
                  'Drop the returned .zip here, or click to choose. Or put a folder in translation/returned/.', input);
                return zone;
              })(),
              ui.lists.returned.length ? h('table', { class: 'data' }, h('tbody', {}, ui.lists.returned.map(x => h('tr', {},
                h('td', { class: 'mono' }, x.name), h('td', { class: 'small' }, `${x.kind} · ${fmtAgo(x.mtime)}`),
                h('td', {}, h('button', { class: 'btn small primary', disabled: ui.busy, onclick: () => doImport(x) }, 'Import and check'))))))
                : h('p', { class: 'muted' }, 'Nothing returned yet.'),
              h('p', { class: 'muted small' }, 'Expected names: <topic_id>.zh.md and <topic_id>.zh.srt.'))),
          importResult())));
  }
  render();
  refresh();
  return null;
}
