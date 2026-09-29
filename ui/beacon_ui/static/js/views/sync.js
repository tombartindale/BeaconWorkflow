// Sync: pull from and push to the shared OneDrive folder. Both directions are shown as
// previews first (bcn sync --dry-run, which never downloads), and a sync only runs when asked.
import { api, awaitJob, confirmModal, fmtAgo, fmtBytes, h, mount, runJob, subscribe, toast } from '../core.js';

const LIMIT = 200;

export function syncView(main) {
  let data = null;
  let error = null;
  let busy = false;

  async function load() {
    try { data = await api('/api/sync'); error = null; } catch (e) { error = e.message; }
    render();
  }

  async function run(direction, extra = {}, label) {
    busy = true; render();
    try {
      const job = await runJob('sync', ['.'], { [direction]: true, ...extra });
      const done = await awaitJob(job.id);
      const env = done.envelopes?.[0];
      if (env) {
        const n = (env.diagnostics || []).filter(d => d.code === 'SYNC_COPIED').length;
        const bad = (env.diagnostics || []).filter(d => d.level === 'error');
        toast(`${label || (direction === 'pull' ? 'Pulled' : 'Pushed')}: ${n} file${n === 1 ? '' : 's'} copied` +
          (bad.length ? `, ${bad.length} need attention` : ''), bad.length > 0);
      }
    } catch (e) { toast(e.message, true); }
    busy = false;
    load();
  }

  function planTable(plan) {
    const rows = plan.slice(0, LIMIT);
    return h('div', {},
      h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['', 'Local', 'OneDrive', 'Why'].map(x => h('th', {}, x)))),
        h('tbody', {}, rows.map(p => h('tr', {},
          h('td', {}, h('span', { class: `chip ${p.action === 'conflict' ? 'error' : p.action === 'copy' ? 'ok' : ''}` },
            { copy: 'copy', conflict: 'conflict', one_side: 'one side', check: 'check', failed: 'failed' }[p.action] || p.action)),
          h('td', { class: 'mono small' }, p.local),
          h('td', { class: 'mono small' }, p.remote, p.cloud ? h('span', { class: 'chip cloud', style: { marginLeft: '6px' } }, '☁') : null),
          h('td', { class: 'small muted' }, p.reason, p.bytes ? ` · ${fmtBytes(p.bytes)}` : ''))))),
      plan.length > LIMIT ? h('p', { class: 'muted small', style: { padding: '0 14px' } }, `…and ${plan.length - LIMIT} more.`) : null);
  }

  function side(env, direction) {
    const pull = direction === 'pull';
    const title = pull ? 'Pull from OneDrive' : 'Push to OneDrive';
    const notConfigured = (env.diagnostics || []).find(d => d.code === 'SYNC_NOT_CONFIGURED' || d.code === 'SYNC_REMOTE_MISSING');
    if (notConfigured) return h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, title)),
      h('div', { class: 'panel-body' }, h('p', {}, notConfigured.message), notConfigured.hint ? h('p', { class: 'muted' }, notConfigured.hint) : null));
    const c = env.counts || {};
    const copies = (env.plan || []).filter(p => p.action === 'copy' || p.action === 'check');
    const conflicts = (env.plan || []).filter(p => p.action === 'conflict');
    const oneSide = (env.plan || []).filter(p => p.action === 'one_side');
    const last = pull ? env.last_pull : env.last_push;
    const cloud = copies.filter(p => p.cloud).length;
    const doRun = async () => {
      const body = h('div', {},
        h('p', {}, `This copies ${copies.length} file${copies.length === 1 ? '' : 's'} ${pull ? 'from OneDrive into the local copy' : 'from the local copy to OneDrive'}.`),
        pull && cloud ? h('p', {}, `${cloud} of them are cloud-only and will be downloaded first.`) : null,
        conflicts.length ? h('p', {}, `${conflicts.length} conflict${conflicts.length === 1 ? '' : 's'} will be left alone.`) : null,
        h('p', { class: 'muted' }, 'Nothing is ever deleted on either side.'));
      if (await confirmModal(`${title}?`, body, pull ? 'Pull' : 'Push')) run(direction);
    };
    return h('div', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, title),
        h('span', { class: 'muted small' }, last ? `last ${pull ? 'pulled' : 'pushed'} ${fmtAgo(last)}` : `never ${pull ? 'pulled' : 'pushed'}`)),
      h('div', { class: 'actions' },
        h('span', {}, h('strong', {}, copies.length), ` to copy · ${c.in_sync || 0} in sync`),
        conflicts.length ? h('span', { class: 'chip error' }, `${conflicts.length} conflict${conflicts.length === 1 ? '' : 's'}`) : null,
        oneSide.length ? h('span', { class: 'chip', title: 'present on one side only; deletions never sync' }, `${oneSide.length} on one side only`) : null,
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn primary', disabled: busy || !copies.length, onclick: doRun },
          copies.length ? `${pull ? 'Pull' : 'Push'} ${copies.length} file${copies.length === 1 ? '' : 's'}` : 'Nothing to copy')),
      (env.plan || []).length ? planTable(env.plan) : h('div', { class: 'empty' }, 'Everything is in sync.'));
  }

  function conflictSection(conflicts) {
    if (!conflicts.length) return null;
    return h('div', { class: 'panel', style: { marginBottom: '16px' } },
      h('div', { class: 'panel-head' }, h('h2', {}, `Changed on both sides (${conflicts.length})`)),
      h('div', { class: 'panel-body' }, h('p', { class: 'muted small', style: { marginTop: 0 } },
        'Neither version has been overwritten. Choose which one to keep; the other side is replaced with it.'),
        h('table', { class: 'data' }, h('tbody', {}, conflicts.map(p => h('tr', {},
          h('td', { class: 'mono small' }, p.local), h('td', { class: 'mono small muted' }, p.remote),
          h('td', {}, h('span', { class: 'row' },
            h('button', { class: 'btn small', disabled: busy, onclick: async () => {
              if (await confirmModal('Keep the OneDrive version?', `The local ${p.local} is replaced with ${p.remote}.`, 'Keep OneDrive version'))
                run('pull', { prefer: 'remote', only: [p.local] }, 'Kept OneDrive version');
            } }, 'Keep OneDrive version'),
            h('button', { class: 'btn small', disabled: busy, onclick: async () => {
              if (await confirmModal('Keep the local version?', `${p.remote} on OneDrive is replaced with the local ${p.local}.`, 'Keep local version'))
                run('push', { prefer: 'local', only: [p.local] }, 'Kept local version');
            } }, 'Keep local version')))))))));
  }

  function render() {
    if (error) { mount(main, h('div', { class: 'banner error' }, error)); return; }
    if (!data) { mount(main, h('div', { class: 'empty' }, 'Comparing with OneDrive…')); return; }
    const remote = data.pull.remote || data.push.remote;
    const ignored = data.pull.ignored || [];
    const copyConflicts = (data.pull.diagnostics || []).filter(d => d.code === 'SYNC_REMOTE_CONFLICT_COPY');
    mount(main,
      h('div', { class: 'page-head' },
        h('div', {}, h('h1', {}, 'Sync'),
          h('div', { class: 'sub' }, remote ? h('span', {}, 'OneDrive folder: ', h('span', { class: 'mono small' }, remote)) :
            'The local working copy and the shared OneDrive folder are synced by hand, in each direction.')),
        h('button', { class: 'btn', disabled: busy, onclick: () => { data = null; render(); load(); } }, 'Check again')),
      copyConflicts.length ? h('div', { class: 'banner error', style: { margin: '0 0 16px' } },
        h('strong', {}, 'OneDrive conflict copies: '), copyConflicts.map(d => d.file).join(', '),
        '. Someone needs to decide which version is right in OneDrive and delete the other; they are not synced.') : null,
      conflictSection((data.pull.plan || []).filter(p => p.action === 'conflict')),
      h('div', { class: 'grid-2' }, side(data.pull, 'pull'), side(data.push, 'push')),
      h('p', { class: 'muted small' },
        'Pull brings scripts, course maps, activities, asset requests and the editor\'s video and subtitles into the local copy. ',
        'Push sends local script changes, review decisions and finished delivery packages back. ',
        'Pulled files are marked as new, so anything built from an older version shows as stale.'),
      ignored.length ? h('details', { class: 'panel', style: { marginTop: '12px' } },
        h('summary', { style: { padding: '10px 14px', cursor: 'pointer' } }, `${ignored.length} files in OneDrive are not part of the pipeline and are left alone`),
        h('ul', { class: 'mono small', style: { margin: '0 0 12px' } }, ignored.slice(0, 300).map(f => h('li', {}, f)))) : null);
  }

  render();
  load();
  const off = subscribe(kind => { if (kind === 'status' && !busy) load(); });
  return { dispose: off };
}
