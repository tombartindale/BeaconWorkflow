// Shared plumbing: API, live events, store, DOM helpers, formatting, modals.
// The UI renders what bcn reports and computes nothing about the pipeline itself.

export const store = {
  boot: null,          // /api/boot
  status: null,        // bcn status envelope for the whole programme
  jobs: new Map(),     // id -> job summary
  codes: new Map(),    // diagnostic code -> {level, description}
  connected: false,
};

const listeners = new Set();
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function notify(kind, detail) { for (const fn of [...listeners]) { try { fn(kind, detail); } catch (e) { console.error(e); } } }

// -- API -------------------------------------------------------------------------------------
export async function api(path, { method = 'GET', body, raw } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (raw) { opts.body = body; opts.headers['Content-Type'] = 'application/octet-stream'; }
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

export async function loadStatus() {
  store.status = await api('/api/status');
  notify('status', store.status);
  return store.status;
}

export async function loadJobs() {
  const { jobs } = await api('/api/jobs?limit=150');
  store.jobs = new Map(jobs.map(j => [j.id, j]));
  notify('jobs');
}

export async function runJob(command, targets, args = {}) {
  const job = await api('/api/jobs', { method: 'POST', body: { command, targets, args } });
  store.jobs.set(job.id, job);
  notify('jobs');
  toast(`Queued: ${job.label}`);
  return job;
}

// Wait for a job to finish, resolving with its full record (envelopes included).
export function awaitJob(id) {
  return new Promise(resolve => {
    const check = async () => {
      const j = store.jobs.get(id);
      if (j && !['queued', 'running', 'stalled'].includes(j.state)) {
        off();
        resolve(await api(`/api/jobs/${id}`));
      }
    };
    const off = subscribe(kind => { if (kind === 'jobs') check(); });
    check();
  });
}

// -- live events -------------------------------------------------------------------------------
let statusTimer = null;
export function connect() {
  const es = new EventSource('/api/events');
  const live = document.getElementById('live');
  const liveText = document.getElementById('live-text');
  es.addEventListener('hello', () => {
    store.connected = true;
    live.className = 'live up';
    liveText.textContent = 'live';
    loadStatus().catch(() => {});
    loadJobs().catch(() => {});
  });
  es.onerror = () => {
    store.connected = false;
    live.className = 'live down';
    liveText.textContent = 'reconnecting';
  };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === 'status') {
      // Coalesce bursts of refreshes into one reload.
      clearTimeout(statusTimer);
      statusTimer = setTimeout(() => loadStatus().catch(() => {}), 150);
    } else if (ev.type === 'job') {
      store.jobs.set(ev.job.id, { ...(store.jobs.get(ev.job.id) || {}), ...ev.job });
      notify('jobs');
    } else if (ev.type === 'job-event') {
      const j = store.jobs.get(ev.job);
      if (j) {
        if (ev.event.event === 'progress') j.progress = ev.event;
        j.target_index = ev.target_index;
        j.last_event_age_ms = 0;
        j._seen = Date.now();
        if (j.state === 'stalled') j.state = 'running';
      }
      notify('job-event', ev);
    } else if (ev.type === 'warnings') {
      store.boot.warnings = ev.warnings;
      renderBanners();
    } else if (ev.type === 'status-error') {
      toast(ev.error, true);
    }
  };
}

// -- DOM ------------------------------------------------------------------------------------------
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function mount(el, ...children) { el.replaceChildren(...children.flat(Infinity).filter(Boolean)); return el; }

export function pips(index, total, cls = '') {
  const el = h('span', { class: `pips ${cls}`, role: 'img', 'aria-label': `stage ${index + 1} of ${total}` });
  for (let i = 0; i < total; i++) el.append(h('i', { class: i <= index ? 'on' : '' }));
  return el;
}

export const STAGE_LABEL = {
  planned: 'Planned', drafted: 'Drafted', validated: 'Validated', rendered: 'Rendered', recorded: 'Recorded',
  cued: 'Cued', packaged: 'Packaged', not_sent: 'Not sent', out_for_translation: 'Out for translation',
  returned: 'Returned', parity_checked: 'Parity checked',
};
export const NEXT_LABEL = {
  intake: 'write the script', validate: 'validate', render: 'render', await_recording: 'awaiting the edit',
  cues: 'cues', subtitles: 'subtitles', package: 'package', translation_export: 'send for translation',
  await_translation: 'awaiting translation',
};
export const RUNNABLE = new Set(['validate', 'render', 'cues', 'subtitles', 'package']);

export function fmtTime(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return '—';
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}
export function fmtTC(sec) {
  const ms = Math.round(sec * 1000);
  const hh = Math.floor(ms / 3600000), mm = Math.floor(ms / 60000) % 60, ss = Math.floor(ms / 1000) % 60, mmm = ms % 1000;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}.${String(mmm).padStart(3, '0')}`;
}
export function fmtDuration(ms) {
  if (ms === null || ms === undefined) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
export function fmtAgo(iso) {
  if (!iso) return '—';
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}
export function fmtBytes(n) {
  if (n === null || n === undefined) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function topicPath(id) { return id.split('-').join('/'); }
export function topicsByModule() {
  const out = new Map();
  for (const r of (store.status?.results || [])) {
    if (!out.has(r.module)) out.set(r.module, []);
    out.get(r.module).push(r);
  }
  return out;
}

// -- modal, toast, banners ------------------------------------------------------------------------
export function confirmModal(title, body, okLabel = 'Run') {
  return new Promise(resolve => {
    const root = document.getElementById('modal-root');
    const close = (v) => { root.replaceChildren(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); if (e.key === 'Enter') close(true); };
    document.addEventListener('keydown', onKey);
    root.addEventListener('route', () => close(false), { once: true });
    const ok = h('button', { class: 'btn primary', onclick: () => close(true) }, okLabel);
    mount(root, h('div', { class: 'modal-back', onclick: (e) => { if (e.target === e.currentTarget) close(false); } },
      h('div', { class: 'modal panel', role: 'dialog', 'aria-modal': 'true' },
        h('h2', {}, title), typeof body === 'string' ? h('p', {}, body) : body,
        h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => close(false) }, 'Cancel'), ok))));
    ok.focus();
  });
}

export function toast(msg, error = false) {
  const el = h('div', { class: `toast${error ? ' error' : ''}` }, msg);
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), error ? 7000 : 3500);
}

export function renderBanners() {
  const b = document.getElementById('banners');
  const items = [];
  for (const w of store.boot?.warnings || []) items.push(h('div', { class: 'banner' }, w));
  for (const r of store.boot?.doctor?.results || []) {
    if (!r.ok) items.push(h('div', { class: 'banner error' }, `Tool check failed for ${r.topic}: `,
      (store.boot.doctor.diagnostics || []).filter(d => true).map(d => d.message).join(' ') ||
      'see bcn doctor.'));
  }
  mount(b, items);
}

export function levelChip(level) { return h('span', { class: `chip ${level}` }, level); }
