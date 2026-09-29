// Script editor: edit topic.md (or topic.zh.md) in the browser, with the problems bcn finds
// shown beside it as you type. bcn edit does all checking and writing: --dry-run to check,
// a job to save. Saving is refused if the file changed on disk since it was loaded.
//
// Keys: ⌘/Ctrl-S save · ⌘/Ctrl-Enter check now · Tab indents.
import { api, awaitJob, confirmModal, h, levelChip, mount, store, toast } from '../core.js';

const LINE_PX = 21;          // must match .ed-text line-height in style.css
const CHECK_DELAY_MS = 700;

export function editorView(main, id, lang = 'en', startLine = null) {
  const draftKey = `beacon-edit-${id}-${lang}`;
  let base = { text: '', sha256: null, exists: false, path: '' };   // what is on disk
  let checkSeq = 0;
  let checkTimer = null;
  let problems = [];
  let summary = null;
  let saving = false;
  let disposed = false;

  const text = h('textarea', { class: 'ed-text', spellcheck: 'true', autocomplete: 'off', 'aria-label': 'Script' });
  const gutter = h('div', { class: 'ed-gutter', 'aria-hidden': 'true' });
  const status = h('span', { class: 'muted small' });
  const side = h('div', { class: 'panel ed-side' });
  const saveBtn = h('button', { class: 'btn primary', onclick: () => save(false) }, 'Save');

  const dirty = () => text.value !== base.text;

  // Long narration lines wrap, so each gutter number is given the height its line actually
  // takes, measured in a hidden copy of the text laid out at the same width.
  const measure = h('div', { class: 'ed-measure', 'aria-hidden': 'true' });
  let lineTops = [];
  function drawGutter() {
    const lines = text.value.split('\n');
    measure.style.width = `${text.clientWidth}px`;
    measure.replaceChildren(...lines.map(l => h('div', {}, l || ' ')));
    const heights = [...measure.children].map(d => d.getBoundingClientRect().height || LINE_PX);
    lineTops = [];
    let top = 0;
    for (const hgt of heights) { lineTops.push(top); top += hgt; }
    const byLine = new Map();
    for (const p of problems) if (p.line) byLine.set(p.line, byLine.get(p.line) === 'error' || p.level === 'error' ? 'error' : p.level);
    const frag = document.createDocumentFragment();
    heights.forEach((hgt, i) => frag.append(h('div', { class: byLine.has(i + 1) ? `ln ${byLine.get(i + 1)}` : 'ln',
      style: { height: `${hgt}px` } }, i + 1)));
    gutter.replaceChildren(frag, h('div', { style: { height: `${text.clientHeight}px` } }));
    gutter.scrollTop = text.scrollTop;
  }
  let gutterFrame = null;
  const redrawGutterSoon = () => { cancelAnimationFrame(gutterFrame); gutterFrame = requestAnimationFrame(drawGutter); };
  const resizeObs = new ResizeObserver(redrawGutterSoon);

  function drawStatus(extra) {
    const errs = problems.filter(p => p.level === 'error').length;
    const warns = problems.filter(p => p.level === 'warn').length;
    status.textContent = [dirty() ? 'Unsaved changes' : 'Saved', extra,
      summary ? `${errs} error${errs === 1 ? '' : 's'} · ${warns} warning${warns === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ');
    saveBtn.disabled = saving || !dirty();
    saveBtn.textContent = saving ? 'Saving…' : 'Save';
  }

  function jumpTo(line) {
    const lines = text.value.split('\n');
    const l = Math.max(1, Math.min(line, lines.length));
    let start = 0;
    for (let i = 0; i < l - 1; i++) start += lines[i].length + 1;
    text.focus();
    text.setSelectionRange(start, start + lines[l - 1].length);
    text.scrollTop = Math.max(0, (lineTops[l - 1] ?? (l - 1) * LINE_PX) - 5 * LINE_PX);
  }

  function drawSide() {
    const order = { error: 0, warn: 1, info: 2 };
    const shown = problems.filter(p => p.code !== 'EDIT_SAVED').sort((a, b) => order[a.level] - order[b.level] || (a.line || 0) - (b.line || 0));
    const s = summary;
    mount(side,
      h('div', { class: 'panel-head' }, h('h2', {}, 'Problems'),
        s ? h('span', { class: 'muted small' }, [s.slides != null ? `${s.slides} slides` : null,
          s.words != null && lang === 'en' ? `${s.words} words` + (s.target_words ? ` / ${s.target_words} target` : '') : null].filter(Boolean).join(' · ')) : null),
      shown.length ? h('ul', { class: 'diaglist' }, shown.map(p => h('li', {},
        h('div', {}, levelChip(p.level)),
        h('div', {},
          p.line ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); jumpTo(p.line); } }, `line ${p.line}: `) : null,
          p.message,
          h('div', { class: 'loc' }, p.code, p.data?.acknowledged ? ' · acknowledged' : ''),
          p.hint ? h('div', { class: 'hint' }, p.hint) : null))))
        : h('div', { class: 'empty' }, summary ? 'No problems.' : 'Checking…'));
  }

  async function check() {
    clearTimeout(checkTimer);
    const seq = ++checkSeq;
    drawStatus('checking…');
    try {
      const env = await api(`/api/topic/${id}/check`, { method: 'POST', body: { text: text.value, lang } });
      if (seq !== checkSeq || disposed) return;   // a newer check is on its way
      problems = env.diagnostics || [];
      summary = env.results?.[0] || {};
    } catch (e) {
      if (seq === checkSeq) toast(`Could not check: ${e.message}`, true);
    }
    drawGutter(); drawSide(); drawStatus();
  }

  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(check, CHECK_DELAY_MS);
    try { sessionStorage.setItem(draftKey, JSON.stringify({ text: text.value, sha256: base.sha256 })); } catch { /* fine */ }
    drawStatus();
  }

  async function save(overwrite) {
    if (saving) return;
    saving = true; drawStatus();
    try {
      const job = await api(`/api/topic/${id}/save`, { method: 'POST',
        body: { text: text.value, lang, expect_sha: base.sha256, overwrite } });
      store.jobs.set(job.id, job);
      const done = await awaitJob(job.id);
      const env = done.envelopes?.[0];
      const conflict = (env?.diagnostics || []).find(d => d.code === 'EDIT_CONFLICT');
      if (conflict) {
        saving = false; drawStatus();
        const body = h('div', {}, h('p', {}, `${base.path} was changed on disk after you opened it (a sync pull or another editor).`),
          h('p', {}, 'Overwrite replaces that version with yours; the replaced version is kept in the topic\'s .history folder. ',
            'Or cancel, copy what you need, and reload the page to see the new version.'));
        if (await confirmModal('Overwrite the newer version?', body, 'Overwrite')) return save(true);
        return;
      }
      if (!env || done.state !== 'done') throw new Error('the save job failed; see Jobs');
      const r = env.results?.[0] || {};
      base = { ...base, text: text.value, sha256: r.sha256 || base.sha256, exists: true };
      try { sessionStorage.removeItem(draftKey); } catch { /* fine */ }
      problems = env.diagnostics || [];
      summary = r;
      const errs = problems.filter(p => p.level === 'error').length;
      toast(r.written === false ? 'No changes to save.' : `Saved${errs ? ` · ${errs} problem${errs === 1 ? '' : 's'} left` : ' · no problems'}`);
    } catch (e) {
      toast(`Not saved: ${e.message}`, true);
    }
    saving = false;
    drawGutter(); drawSide(); drawStatus();
  }

  // -- input handling ------------------------------------------------------------------
  text.addEventListener('input', () => { redrawGutterSoon(); scheduleCheck(); });
  text.addEventListener('scroll', () => { gutter.scrollTop = text.scrollTop; });
  text.addEventListener('keydown', (e) => {
    if (e.key === 'Tab' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      const { selectionStart: s, selectionEnd: en } = text;
      text.setRangeText('  ', s, en, 'end');
      text.dispatchEvent(new Event('input'));
    }
  });
  const onKey = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty()) save(false); }
    else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); check(); }
  };
  document.addEventListener('keydown', onKey);
  const onUnload = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', onUnload);

  // -- layout --------------------------------------------------------------------------
  const file = lang === 'zh' ? 'topic.zh.md' : 'topic.md';
  mount(main,
    h('div', { class: 'page-head' },
      h('div', {},
        h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Programme'), ' / ',
          h('a', { href: `#/module/${id.slice(0, 6)}` }, id.slice(0, 6)), ' / ', h('a', { href: `#/topic/${id}` }, id)),
        h('h1', {}, `Edit ${file}`),
        h('div', { class: 'sub' }, status)),
      h('div', { class: 'row' },
        h('span', { class: 'controls' }, h('span', { class: 'seg' }, ['en', 'zh'].map(l => h('button', {
          class: l === lang ? 'on' : '', onclick: () => { if (l !== lang) location.hash = `#/edit/${id}?lang=${l}`; } }, l === 'en' ? 'English' : 'Mandarin')))),
        h('button', { class: 'btn', title: '⌘/Ctrl-Enter', onclick: check }, 'Check now'),
        h('button', { class: 'btn', onclick: async () => {
          if (!dirty() || await confirmModal('Discard your changes?', 'The editor goes back to the version on disk.', 'Discard')) {
            text.value = base.text; try { sessionStorage.removeItem(draftKey); } catch { /* fine */ } drawGutter(); check();
          }
        } }, 'Revert'),
        saveBtn,
        h('a', { class: 'btn', href: `#/topic/${id}` }, 'Back to topic'))),
    h('div', { class: 'ed-wrap' },
      h('div', { class: 'panel ed-main' }, h('div', { class: 'ed-box' }, gutter, text, measure),
        h('div', { class: 'ed-help muted small' }, '⌘S save · ⌘↵ check · slides are separated by a line of ---; narration goes in a final > **Say:** block')),
      side));

  (async () => {
    try { base = await api(`/api/topic/${id}/source?lang=${lang}`); } catch (e) { toast(e.message, true); return; }
    if (disposed) return;
    let recovered = null;
    try { recovered = JSON.parse(sessionStorage.getItem(draftKey) || 'null'); } catch { /* fine */ }
    if (recovered && recovered.text !== base.text && recovered.sha256 === base.sha256) {
      text.value = recovered.text;
      toast('Recovered your unsaved changes from before.');
    } else {
      text.value = base.text;
    }
    if (!base.exists) text.placeholder = `${file} does not exist yet. Paste or write the script here and save to create it.`;
    resizeObs.observe(text);
    drawGutter(); drawStatus();
    await check();
    if (startLine) jumpTo(startLine); else text.focus();
  })();

  return {
    dispose() {
      disposed = true;
      clearTimeout(checkTimer);
      resizeObs.disconnect();
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('beforeunload', onUnload);
      if (dirty()) toast('Unsaved changes are kept in this tab: reopen the editor to carry on.');
    },
  };
}
