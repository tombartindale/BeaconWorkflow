// Programme: the landing screen. Four numbers, then one row per module. Rows are links and nothing else.
import { h, mount, store, subscribe, STAGE_LABEL } from '../core.js';

// A neutral ramp: later stages darker. Colour is kept for stale and blocked elsewhere.
const RAMP = {
  en: ['#d9d5cc', '#c3cbd2', '#a6b4c0', '#8799a9', '#6a8093', '#4d677d', '#2f4b62'],
  zh: ['#d9d5cc', '#c3cbd2', '#a6b4c0', '#7c91a3', '#557087', '#2f4b62'],
};

function stackbar(counts, stages, lang, total) {
  const bar = h('div', { class: 'stackbar', role: 'img',
    'aria-label': stages.map(s => `${STAGE_LABEL[s]} ${counts[s] || 0}`).join(', ') });
  stages.forEach((s, i) => {
    const n = counts[s] || 0;
    if (!n) return;
    bar.append(h('span', { style: { width: `${(100 * n) / total}%`, background: RAMP[lang][i] },
      title: `${STAGE_LABEL[s]}: ${n}` }));
  });
  return bar;
}

function kpi(value, label, sub, alert = false) {
  return h('div', { class: `panel kpi${alert ? ' alert' : ''}` },
    h('div', { class: 'v' }, value), h('div', { class: 'l' }, label), sub ? h('div', { class: 's' }, sub) : null);
}

export function programmeView(main) {
  const render = () => {
    const env = store.status;
    if (!env) { mount(main, h('div', { class: 'empty' }, 'Reading the programme…')); return; }
    const s = env.summary;
    const running = [...store.jobs.values()].filter(j => ['running', 'stalled'].includes(j.state)).length;
    const queued = [...store.jobs.values()].filter(j => j.state === 'queued').length;
    const stagesEn = env.results[0]?.en.stages || [];
    const stagesZh = env.results[0]?.zh.stages || [];
    const outstanding = s.diagnostics.error + s.diagnostics.warn;

    const rows = Object.entries(s.modules).sort(([a], [b]) => a.localeCompare(b)).map(([name, m]) =>
      h('a', { class: 'mod', href: `#/module/${name}` },
        h('div', {}, h('div', { class: 'name' }, name), h('div', { class: 'title' }, m.title || ''),
          h('div', { class: 'title' }, `${m.topics} topics · ${m.units.length} units`)),
        h('div', { class: 'bars' },
          h('div', { class: 'barrow' }, h('span', { class: 'lang' }, 'EN'), stackbar(m.en, stagesEn, 'en', m.topics),
            h('span', { class: 'n' }, `${m.complete.en}/${m.topics} complete`)),
          h('div', { class: 'barrow' }, h('span', { class: 'lang' }, 'ZH'), stackbar(m.zh, stagesZh, 'zh', m.topics),
            h('span', { class: 'n' }, `${m.complete.zh}/${m.topics} complete`))),
        h('div', { class: 'flags' },
          m.blocked ? h('span', { class: 'chip blocked' }, `${m.blocked} blocked`) : null,
          m.stale ? h('span', { class: 'chip stale' }, `${m.stale} stale`) : null,
          m.cloud ? h('span', { class: 'chip cloud' }, `☁ ${m.cloud} cloud-only`) : null,
          m.unreviewed ? h('span', { class: 'chip warn' }, `${m.unreviewed} to proofread`) : null,
          m.errors ? h('span', { class: 'chip error' }, `course map: ${m.errors} errors`) : null)));

    const legend = (stages, lang) => h('div', { class: 'legend' }, h('strong', {}, lang.toUpperCase()),
      stages.map((st, i) => h('span', {}, h('i', { style: { background: RAMP[lang][i] } }), STAGE_LABEL[st])));

    mount(main,
      h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Programme'),
        h('div', { class: 'sub' }, `${s.topics} topics across ${Object.keys(s.modules).length} modules`))),
      h('div', { class: 'kpis' },
        kpi(`${s.complete.en} / ${s.complete.zh}`, 'Topics complete', 'English / Mandarin'),
        kpi(s.blocked, 'Topics blocked', 'need a person', s.blocked > 0),
        kpi(running, 'Jobs running', queued ? `${queued} queued` : 'none queued'),
        kpi(outstanding, 'Diagnostics outstanding', `${s.diagnostics.error} errors · ${s.diagnostics.warn} warnings`
          + (s.unreviewed ? ` · ${s.unreviewed} to proofread` : ''), s.diagnostics.error > 0)),
      h('div', { class: 'panel modules' }, rows.length ? rows : h('div', { class: 'empty' }, 'No modules found.'),
        legend(stagesEn, 'en'), legend(stagesZh, 'zh')));
  };
  render();
  const off = subscribe(kind => { if (kind === 'status' || kind === 'jobs') render(); });
  return { dispose: off };
}
