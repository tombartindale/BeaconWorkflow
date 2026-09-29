import { api, connect, installTooltips, loadJobs, loadStatus, notify, renderBanners, store, subscribe } from './core.js';
import { programmeView } from './views/programme.js';
import { moduleView } from './views/module.js';
import { topicView } from './views/topic.js';
import { diagnosticsView } from './views/diagnostics.js';
import { jobsView } from './views/jobs.js';
import { intakeView } from './views/intake.js';
import { translationView } from './views/translation.js';
import { settingsView } from './views/settings.js';
import { syncView } from './views/sync.js';
import { docView } from './views/doc.js';
import { prompterView } from './views/prompter.js';
import { editorView } from './views/editor.js';

const main = document.getElementById('main');
let current = null; // { dispose() }

const routes = [
  [/^#?\/?$/, () => programmeView(main), 'programme'],
  [/^#\/module\/([A-Z]{2}\d{4})$/, (m) => moduleView(main, m[1]), 'programme'],
  [/^#\/topic\/([A-Z]{2}\d{4}-U\d{2}-T\d{2})(?:\?t=([\d.]+))?$/, (m) => topicView(main, m[1], m[2] ? Number(m[2]) : null), 'programme'],
  [/^#\/diagnostics(?:\/(mistranscriptions))?$/, (m) => diagnosticsView(main, m[1] || 'codes'), 'diagnostics'],
  [/^#\/jobs(?:\/(\d+))?$/, (m) => jobsView(main, m[1] ? Number(m[1]) : null), 'jobs'],
  [/^#\/intake$/, () => intakeView(main), 'intake'],
  [/^#\/translation$/, () => translationView(main), 'translation'],
  [/^#\/sync$/, () => syncView(main), 'sync'],
  [/^#\/prompt\/([A-Z]{2}\d{4}-U\d{2}-T\d{2})$/, (m) => prompterView(main, m[1]), 'programme'],
  [/^#\/edit\/([A-Z]{2}\d{4}-U\d{2}-T\d{2})(?:\?lang=(en|zh))?(?:[?&]line=(\d+))?$/, (m) => editorView(main, m[1], m[2] || 'en', m[3] ? Number(m[3]) : null), 'programme'],
  [/^#\/doc\/([A-Z]{2}\d{4}\/[^?]+?)(?:\?line=(\d+))?$/, (m) => docView(main, decodeURIComponent(m[1]), m[2] ? Number(m[2]) : null), 'programme'],
  [/^#\/settings$/, () => settingsView(main), 'settings'],
];

function route() {
  const hash = location.hash || '#/';
  for (const [rx, fn, nav] of routes) {
    const m = hash.match(rx);
    if (m) {
      if (current && current.dispose) current.dispose();
      // Any open modal belongs to the old view.
      document.getElementById('modal-root').dispatchEvent(new Event('route'));
      current = fn(m) || null;
      document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === nav));
      main.focus({ preventScroll: true });
      return;
    }
  }
  location.hash = '#/';
}

function updateNavCount() {
  const running = [...store.jobs.values()].filter(j => ['running', 'queued', 'stalled'].includes(j.state)).length;
  document.getElementById('nav-jobs').textContent = running ? String(running) : '';
}

async function start() {
  try {
    store.boot = await api('/api/boot');
  } catch (e) {
    main.textContent = `The backend is not reachable: ${e.message}`;
    return;
  }
  store.codes = new Map((store.boot.codes || []).map(c => [c.code, c]));
  installTooltips();
  renderBanners();
  subscribe(kind => { if (kind === 'jobs' || kind === 'job-event') updateNavCount(); });
  window.addEventListener('hashchange', route);
  connect();
  await Promise.allSettled([loadStatus(), loadJobs()]);
  route();
  // Stalled detection is time-based, so re-evaluate running jobs every few seconds.
  setInterval(() => notify('tick'), 2000);
}

start();
