// Diagnostics: everything outstanding, grouped by code, because failures cluster and
// fixing a class of them at once is how the work goes. Mis-transcriptions get their own
// tab: a proofreading task, with both readings side by side.
import { ackControls, api, fmtTime, h, levelChip, mount, runJob, store, subscribe, topicPath } from '../core.js';

export function diagnosticsView(main, tab) {
  const f = { level: 'warn', module: '', lang: '', code: '' };
  let env = null;
  let error = null;

  async function load() {
    try { env = await api('/api/diagnostics?path=.'); error = null; } catch (e) { error = e.message; }
    render();
  }

  function filtered() {
    const order = { error: 0, warn: 1, info: 2 };
    return (env?.outstanding || []).filter(d =>
      order[d.level] <= order[f.level] &&
      (!f.module || (d.topic || d.file || '').startsWith(f.module)) &&
      (!f.lang || d.lang === f.lang) &&
      (!f.code || d.code === f.code));
  }

  function codesTab() {
    const list = filtered().filter(d => d.code !== 'CUE_MISTRANSCRIPTION' || d.level !== 'info');
    const groups = new Map();
    for (const d of list) {
      if (!groups.has(d.code)) groups.set(d.code, []);
      groups.get(d.code).push(d);
    }
    const sorted = [...groups.entries()].sort((a, b) => {
      const o = { error: 0, warn: 1, info: 2 };
      return (o[a[1][0].level] - o[b[1][0].level]) || (b[1].length - a[1].length);
    });
    if (!sorted.length) return h('div', { class: 'panel empty' }, 'Nothing outstanding at this level.');
    return sorted.map(([code, items]) => {
      const topics = [...new Set(items.map(d => d.topic).filter(Boolean))].sort();
      const steps = [...new Set(items.map(d => d.data?.step).filter(Boolean))];
      const langs = [...new Set(items.map(d => d.lang).filter(Boolean))];
      const step = steps.length === 1 ? steps[0] : null;
      const rerun = step && topics.length && !['qa'].includes(step) ? h('button', {
        class: 'btn small', onclick: (e) => {
          e.preventDefault();
          const lang = step === 'cues' ? 'en' : (langs.length === 1 ? langs[0] : 'en');
          runJob(step, topics.map(topicPath), { lang });
        } }, `Re-run ${step} on ${topics.length}`) : null;
      return h('details', { class: 'panel group' },
        h('summary', {},
          h('span', { class: 'n' }, items.length),
          h('span', {}, levelChip(items[0].level), ' ', h('code', {}, code)),
          h('span', { class: 'desc' }, store.codes.get(code)?.description || ''),
          rerun),
        h('div', { class: 'topics' }, topics.map(t => h('a', { class: 'chip', href: `#/topic/${t}` }, t))),
        h('ul', { class: 'diaglist' }, items.slice(0, 200).map(d => h('li', {},
          h('div', {}, d.lang ? h('span', { class: 'chip' }, d.lang.toUpperCase()) : null),
          h('div', {},
            h('div', {}, d.topic ? h('a', { href: `#/topic/${d.topic}` }, d.topic) : null,
              !d.topic && d.data?.document && d.file ? h('a', { href: `#/doc/${d.file}${d.line ? '?line=' + d.line : ''}` }, d.file) : null,
              d.topic || (d.data?.document && d.file) ? ' · ' : '', d.message),
            h('div', { class: 'loc' }, [d.file ? `${d.file}${d.line ? ':' + d.line : ''}` : null, d.slide ? `slide ${d.slide}` : null,
              d.data?.step ? `from ${d.data.step}` : null].filter(Boolean).join(' · ')),
            d.hint ? h('div', { class: 'hint' }, d.hint) : null, ackControls(d))))));
    });
  }

  function mtTab() {
    const items = (env?.outstanding || []).filter(d => d.code === 'CUE_MISTRANSCRIPTION' &&
      (!f.module || (d.topic || '').startsWith(f.module)));
    const open = items.filter(d => !d.data?.review);
    const done = items.filter(d => d.data?.review);
    if (!items.length) return h('div', { class: 'panel empty' }, 'No suspected mis-transcriptions.');
    const row = (d) => {
      const dv = d.data;
      const rev = dv.review;
      const input = h('input', { type: 'text', value: rev?.text || dv.script, 'aria-label': 'Corrected subtitle text' });
      const path = topicPath(d.topic);
      return h('div', { class: `mt-row${rev ? ' decided' : ''}` },
        h('div', {}, h('a', { href: `#/topic/${d.topic}` }, d.topic), h('div', { class: 'muted small' },
          `slide ${dv.slide} · cue ${dv.cue ?? '—'} · `,
          dv.time !== null && dv.time !== undefined ? h('a', { href: `#/topic/${d.topic}?t=${dv.time}` }, `play ${fmtTime(dv.time)}`) : null)),
        h('div', { class: 'reading' }, h('span', { class: 'k' }, 'Script (approved)'), dv.script),
        h('div', { class: 'reading heard' }, h('span', { class: 'k' }, 'SRT (captioned)'), dv.srt),
        rev ? h('div', { class: 'decide' }, h('span', { class: 'chip ok' }, rev.decision === 'accept' ? 'accepted' : `corrected to “${rev.text}”`),
          h('span', { class: 'muted small' }, rev.by || ''),
          h('button', { class: 'btn small', onclick: () => runJob('review', [path], { item: dv.id, clear: true }) }, 'undo'))
          : h('div', { class: 'decide' },
            h('button', { class: 'btn small', title: 'The SRT reading is fine as it is', onclick: () => runJob('review', [path], { item: dv.id, accept: true }) }, 'Accept'),
            input,
            h('button', { class: 'btn small primary', title: 'The delivered subtitle should read this', onclick: () => {
              if (input.value.trim()) runJob('review', [path], { item: dv.id, correct: input.value.trim() });
            } }, 'Correct')));
    };
    return h('div', { class: 'stack' },
      h('p', { class: 'muted' }, 'The partner translates from this SRT, so a mishearing here reaches Mandarin. ',
        'Accept leaves the SRT as it is; Correct changes the delivered subtitle text (timings never change). Run subtitles and package afterwards.'),
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, `To review (${open.length})`)),
        open.length ? open.map(row) : h('div', { class: 'empty' }, 'All reviewed.')),
      done.length ? h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, `Reviewed (${done.length})`)), done.map(row)) : null);
  }

  function render() {
    const modules = Object.keys(store.status?.summary?.modules || {}).sort();
    const codes = [...new Set((env?.outstanding || []).map(d => d.code))].sort();
    const mt = (env?.outstanding || []).filter(d => d.code === 'CUE_MISTRANSCRIPTION' && !d.data?.review).length;
    mount(main,
      h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Diagnostics'),
        h('div', { class: 'sub' }, env ? `${env.counts.error} errors · ${env.counts.warn} warnings · ${env.counts.info} info, from current step results` : 'Loading…'))),
      error ? h('div', { class: 'banner error' }, error) : null,
      h('div', { class: 'tabs' },
        h('button', { class: tab === 'codes' ? 'on' : '', onclick: () => { location.hash = '#/diagnostics'; } }, 'By code'),
        h('button', { class: tab !== 'codes' ? 'on' : '', onclick: () => { location.hash = '#/diagnostics/mistranscriptions'; } },
          `Mis-transcriptions${mt ? ` (${mt})` : ''}`)),
      h('div', { class: 'filters panel', style: { marginBottom: '12px' } },
        tab === 'codes' ? h('select', { 'aria-label': 'Level', onchange: e => { f.level = e.target.value; render(); } },
          [['error', 'errors'], ['warn', 'errors and warnings'], ['info', 'everything']].map(([v, l]) => h('option', { value: v, selected: f.level === v }, l))) : null,
        h('select', { 'aria-label': 'Module', onchange: e => { f.module = e.target.value; render(); } },
          h('option', { value: '' }, 'all modules'), modules.map(m => h('option', { value: m, selected: f.module === m }, m))),
        tab === 'codes' ? h('select', { 'aria-label': 'Language', onchange: e => { f.lang = e.target.value; render(); } },
          [['', 'both languages'], ['en', 'English'], ['zh', 'Mandarin']].map(([v, l]) => h('option', { value: v, selected: f.lang === v }, l))) : null,
        tab === 'codes' ? h('select', { 'aria-label': 'Code', onchange: e => { f.code = e.target.value; render(); } },
          h('option', { value: '' }, 'all codes'), codes.map(c => h('option', { value: c, selected: f.code === c }, c))) : null,
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn small', onclick: load }, 'Refresh')),
      env ? (tab === 'codes' ? codesTab() : mtTab()) : h('div', { class: 'empty' }, 'Loading…'));
  }

  render();
  load();
  let t = null;
  const off = subscribe(kind => { if (kind === 'status') { clearTimeout(t); t = setTimeout(load, 300); } });
  return { dispose: off };
}
