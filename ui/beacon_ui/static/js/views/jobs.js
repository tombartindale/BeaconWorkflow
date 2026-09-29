// Jobs: running and recent. Two progress bars (run and current topic), ETA from bcn,
// a live log tail, and cancel. No heartbeat for ten seconds reads as stalled.
import { api, fmtAgo, fmtDuration, h, mount, store, subscribe } from '../core.js';

const LIVE = ['queued', 'running', 'stalled'];

function stateOf(j) {
  if (j.state === 'running' && j._seen && Date.now() - j._seen > 10000) return 'stalled';
  return j.state;
}

function logLine(line) {
  let ev;
  try { ev = JSON.parse(line); } catch { return h('div', {}, line); }
  if (ev.event === 'progress') {
    if (ev.heartbeat) return null;
    return h('div', { class: 'prog' }, `${ev.topic || ''} ${ev.topic_pct ?? ''}${ev.topic_pct !== null && ev.topic_pct !== undefined ? '%' : ''} ${ev.message || ''}`);
  }
  if (ev.event === 'done') return h('div', { class: 'prog' }, ev.cancelled ? '— cancelled —' : '— done —');
  return h('div', { class: ev.level === 'error' ? 'err' : '' }, ev.message || line);
}

export function jobsView(main, openId) {
  const open = new Set(openId ? [openId] : []);
  const details = new Map(); // id -> full job (log, envelopes)

  async function fetchDetail(id) {
    try { details.set(id, await api(`/api/jobs/${id}`)); } catch { /* gone */ }
    render();
  }

  function jobRow(j) {
    const st = stateOf(j);
    const p = j.progress || {};
    const live = LIVE.includes(st);
    const runPct = j.target_count > 1 ? ((j.target_index + (p.pct || 0) / 100) / j.target_count) * 100 : (p.pct || 0);
    const d = details.get(j.id);
    const env = d?.envelopes || [];
    const diags = env.flatMap(e => e.diagnostics || []).filter(x => x.level !== 'info');
    return h('div', { class: 'job' },
      h('div', { class: 'top' },
        h('span', { class: `chip ${st}` }, st),
        h('span', { class: 'label' }, j.label),
        h('span', { class: 'meta' }, `#${j.id} · ${j.by || ''} · ${live ? (j.started ? `started ${fmtAgo(j.started)}` : 'waiting') : `finished ${fmtAgo(j.finished)} · ${fmtDuration(j.duration_ms)}`}`),
        j.exit_code ? h('span', { class: 'meta' }, `exit ${j.exit_code}`) : null,
        h('span', { class: 'spacer' }),
        live ? h('button', { class: 'btn small danger', onclick: () => api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }) }, 'Cancel') : null,
        h('button', { class: 'btn small', onclick: () => {
          if (open.has(j.id)) open.delete(j.id); else { open.add(j.id); fetchDetail(j.id); }
          render();
        } }, open.has(j.id) ? 'Hide' : 'Details')),
      live && j.state !== 'queued' ? h('div', {},
        h('div', { class: 'progress' }, h('span', {}, 'Run'), h('div', { class: 'pbar' }, h('span', { style: { width: `${runPct.toFixed(1)}%` } })),
          h('span', {}, `${runPct.toFixed(0)}%`)),
        h('div', { class: 'progress' }, h('span', {}, 'Topic'), h('div', { class: 'pbar topic' }, h('span', { style: { width: `${p.topic_pct || 0}%` } })),
          h('span', {}, p.topic_pct !== null && p.topic_pct !== undefined ? `${Math.round(p.topic_pct)}%` : '')),
        h('div', { class: 'meta' }, [p.topic, p.message, p.items ? `topic ${p.item} of ${p.items}` : null,
          j.target_count > 1 ? `invocation ${j.target_index + 1} of ${j.target_count}` : null,
          p.elapsed_ms ? `elapsed ${fmtDuration(p.elapsed_ms)}` : null,
          p.eta_ms !== undefined ? `ETA ${fmtDuration(p.eta_ms)}` : null,
          st === 'stalled' ? 'no heartbeat for over 10 s' : null].filter(Boolean).join(' · '))) : null,
      open.has(j.id) ? h('div', {},
        diags.length ? h('ul', { class: 'diaglist', style: { marginTop: '8px' } }, diags.slice(0, 50).map(x => h('li', {},
          h('div', {}, h('span', { class: `chip ${x.level}` }, x.level)),
          h('div', {}, x.topic ? h('a', { href: `#/topic/${x.topic}` }, x.topic) : null, x.topic ? ' · ' : '', x.message,
            h('div', { class: 'loc' }, x.code))))) : null,
        h('div', { class: 'log' }, (d?.log || []).slice(-300).map(logLine))) : null);
  }

  function render() {
    const jobs = [...store.jobs.values()].sort((a, b) => b.id - a.id);
    const live = jobs.filter(j => LIVE.includes(stateOf(j)));
    const recent = jobs.filter(j => !LIVE.includes(stateOf(j))).slice(0, 60);
    mount(main,
      h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Jobs'),
        h('div', { class: 'sub' }, 'Every action is a job. Jobs never run twice on the same topic at once; cancelling lets bcn clean up.'))),
      h('div', { class: 'panel', style: { marginBottom: '16px' } }, h('div', { class: 'panel-head' }, h('h2', {}, `Running and queued (${live.length})`)),
        live.length ? live.map(jobRow) : h('div', { class: 'empty' }, 'Nothing running.')),
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Recent')),
        recent.length ? recent.map(jobRow) : h('div', { class: 'empty' }, 'No jobs yet.')));
  }

  for (const id of open) fetchDetail(id);
  render();
  let pending = null;
  const off = subscribe((kind, ev) => {
    if (kind === 'job-event' && open.has(ev.job)) {
      const d = details.get(ev.job);
      if (d) d.log = [...(d.log || []), JSON.stringify(ev.event)].slice(-400);
    }
    if (kind === 'jobs') for (const id of open) {
      const j = store.jobs.get(id);
      if (j && !LIVE.includes(j.state) && !details.get(id)?.finished) fetchDetail(id);
    }
    if (['jobs', 'job-event', 'tick'].includes(kind) && !pending) pending = requestAnimationFrame(() => { pending = null; render(); });
  });
  return { dispose: off };
}
