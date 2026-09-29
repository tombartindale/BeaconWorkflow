// Topic: everything about one topic on one page. Script, slides and video panes, then
// artefacts and diagnostics, with actions to run any step. The video pane is how a
// mistimed cue gets found: ten seconds of watching beats any report.
import {
  api, awaitJob, confirmModal, fmtAgo, fmtBytes, fmtTC, fmtTime, h, levelChip, mount, NEXT_LABEL, pips, runJob,
  STAGE_LABEL, store, subscribe, toast, topicPath, ackControls,
} from '../core.js';

const STEPS = ['validate', 'render', 'cues', 'subtitles', 'compose', 'package', 'qa'];

export function topicView(main, id, startAt) {
  const rel = topicPath(id);
  const ui = {
    lang: 'en', force: false, noBumpers: true,
    layout: store.boot.prefs.topic_layout || 'side',
    scriptLang: 'en', subs: 'en', source: 'master', safeArea: false,
  };
  let data = null;         // {show, status}
  let verify = null;       // status --verify envelope
  let diags = [];          // outstanding diagnostics for this topic
  let disposed = false;
  // Cache-bust each URL with its own file's mtime, so media reloads only when that file changes.
  const stamp = (path) => {
    const a = (data?.status?.artifacts || []).find(x => x.path === path || (x.kind === 'slides' && path.startsWith(x.path + '/')));
    return a?.mtime ? encodeURIComponent(a.mtime) : '0';
  };

  const els = {
    head: h('div'), actions: h('div', { class: 'panel' }), panes: h('div', { class: `panes ${ui.layout}` }),
    script: h('div', { class: 'panel' }), slides: h('div', { class: 'panel' }), video: h('div', { class: 'panel' }),
    artefacts: h('div', { class: 'panel' }), diags: h('div', { class: 'panel' }),
  };
  els.panes.append(els.script, els.slides, els.video);
  mount(main, els.head, els.actions, els.panes, h('div', { class: 'grid-2' }, els.diags, els.artefacts));

  // -- data ------------------------------------------------------------------------------
  async function load() {
    try {
      data = await api(`/api/topic/${id}`);
      const d = await api(`/api/diagnostics?path=${encodeURIComponent(rel)}`);
      diags = d.outstanding || [];
    } catch (e) {
      mount(els.head, h('div', { class: 'banner error' }, e.message));
      return;
    }
    if (disposed) return;
    renderHead(); renderActions(); renderScript(); renderSlides(); player.update(); renderArtefacts(); renderDiags();
  }
  async function runVerify() {
    verify = null; renderArtefacts();
    try { verify = await api(`/api/topic/${id}?verify=1`); } catch (e) { toast(e.message, true); }
    if (!disposed) { renderArtefacts(); renderDiags(); }
  }

  // -- head -------------------------------------------------------------------------------
  function langState(s, lang) {
    return h('div', {},
      h('span', { class: 'lang' }, lang.toUpperCase()), pips(s.stage_index, s.stages.length, 'lg'),
      h('span', {}, STAGE_LABEL[s.stage]),
      s.stale ? h('span', { class: 'chip stale', title: s.stale_steps.join(', ') }, `stale: ${s.stale_steps.join(', ')}`) : null,
      s.blocked ? h('span', { class: 'chip blocked' }, 'blocked') : null,
      s.complete ? h('span', { class: 'chip ok' }, 'complete') : null,
      s.next ? h('span', { class: 'muted small' }, `next: ${NEXT_LABEL[s.next] || s.next}`) : null);
  }
  function renderHead() {
    const st = data.status;
    const show = data.show.results?.[0] || {};
    const title = st?.title || show.en?.front?.title || '';
    const blockers = st ? [...st.en.blockers.map(b => ({ ...b, lang: 'EN' })), ...st.zh.blockers.map(b => ({ ...b, lang: 'ZH' }))] : [];
    mount(els.head, h('div', { class: 'topic-head' },
      h('div', {},
        h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Programme'), ' / ',
          h('a', { href: `#/module/${id.slice(0, 6)}` }, id.slice(0, 6)), ' / ', id.slice(7, 10)),
        h('h1', {}, `${id} · ${title}`),
        st ? h('div', { class: 'langstate' }, langState(st.en, 'en'), langState(st.zh, 'zh'),
          st.hydration !== 'local' && st.hydration !== 'unknown' ? h('span', { class: 'chip cloud' }, `☁ ${st.hydration}`) : null,
          st.minutes ? h('span', { class: 'muted small' }, `${st.minutes} min · ${st.outcomes.join(', ')}`) : null) : null,
        blockers.length ? h('ul', { class: 'blockers' }, blockers.map(b =>
          h('li', {}, h('code', {}, `${b.lang} ${b.code}`), b.message, b.code === 'FS_NOT_HYDRATED'
            ? h('span', { class: 'muted' }, ' — in Finder, choose Always Keep on This Device for this folder.') : null))) : null),
      h('div', { class: 'row' },
        h('div', { class: 'controls' }, h('span', { class: 'muted small' }, 'Layout'),
          h('span', { class: 'seg' }, ['side', 'stacked'].map(l => h('button', { class: ui.layout === l ? 'on' : '',
            onclick: () => { ui.layout = l; els.panes.className = `panes ${l}`; renderHead(); } }, l === 'side' ? 'side by side' : 'stacked')))))));
  }

  // -- actions ----------------------------------------------------------------------------
  function renderActions() {
    const st = data.status;
    const next = st ? (ui.lang === 'zh' ? st.zh.next : st.en.next) : null;
    const run = async (step) => {
      const args = { lang: step === 'cues' ? 'en' : ui.lang };
      if (ui.force) args.force = true;
      if (step === 'compose' && ui.noBumpers) args.no_bumpers = true;
      if (step === 'qa') delete args.lang;
      await runJob(step, [rel], args);
    };
    mount(els.actions, h('div', { class: 'actions' },
      h('span', { class: 'controls' }, h('span', { class: 'seg' }, ['en', 'zh'].map(l => h('button', {
        class: ui.lang === l ? 'on' : '', onclick: () => { ui.lang = l; renderActions(); } }, l === 'en' ? 'English' : 'Mandarin')))),
      h('span', { class: 'sep' }),
      STEPS.map(s => h('button', {
        class: `btn${next === s ? ' next' : ''}`, title: next === s ? 'This is the next step' : '',
        disabled: (s === 'cues' && ui.lang === 'zh') || (s === 'qa' && ui.lang === 'zh'),
        onclick: () => run(s) }, s)),
      h('span', { class: 'sep' }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.force, onchange: e => { ui.force = e.target.checked; } }), 'force re-run'),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.noBumpers, onchange: e => { ui.noBumpers = e.target.checked; } }), 'no bumpers'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', title: 'The narration as a large-print PDF, plus copies for a teleprompter', onclick: async () => {
        const job = await runJob('script', [rel], ui.force ? { force: true } : {});
        const done = await awaitJob(job.id);
        await load();
        if (done.state === 'done') toast('Recording script ready: see the links at the top of the Script pane.');
        else toast('The recording script could not be made; see Jobs.', true);
      } }, 'Recording script'),
      h('button', { class: 'btn', title: `Title-card intro and outro videos, in ${ui.lang === 'en' ? 'English' : 'Mandarin'} (the language chosen on the left)`, onclick: async () => {
        const args = { lang: ui.lang, ...(ui.force ? { force: true } : {}) };
        const job = await runJob('bumpers', [rel], args);
        const done = await awaitJob(job.id);
        await load();
        if (done.state === 'done') toast('Intro and outro ready: see the top of the Slides pane.');
        else toast('The intro and outro could not be made; see Jobs.', true);
      } }, 'Intro/outro'),
      h('a', { class: 'btn', href: `#/prompt/${id}`, target: '_blank', title: 'Open the narration as a full-screen teleprompter in a new tab' }, 'Teleprompter'),
      h('button', { class: 'btn', onclick: runVerify }, 'Verify files'),
      h('a', { class: 'btn', href: `#/jobs` }, 'Jobs')));
  }

  // -- script -----------------------------------------------------------------------------
  function renderScript() {
    const show = data.show.results?.[0];
    const s = show?.[ui.scriptLang];
    const scriptFiles = (data.status?.artifacts || []).filter(a => a.kind === 'script' && a.exists);
    const label = { pdf: 'PDF', html: 'for teleprompter', txt: 'text' };
    const head = h('div', { class: 'panel-head' }, h('span', { class: 'row' }, h('h2', {}, 'Script'),
        h('a', { class: 'btn small', href: `#/edit/${id}?lang=${ui.scriptLang}`, title: 'Edit the script and slides' }, 'Edit')),
      scriptFiles.length ? h('span', { class: 'small row' }, 'Recording script:',
        scriptFiles.map(a => h('a', { href: `/files/${a.path}?v=${stamp(a.path)}`, target: '_blank',
          ...(a.path.endsWith('.pdf') ? {} : { download: a.path.split('/').pop() }) }, label[a.path.split('.').pop()])),
        scriptFiles[0].stale ? h('span', { class: 'chip stale' }, 'out of date') : null) : null,
      show?.zh ? h('span', { class: 'controls' }, h('span', { class: 'seg' }, ['en', 'zh'].map(l => h('button', {
        class: ui.scriptLang === l ? 'on' : '', onclick: () => { ui.scriptLang = l; renderScript(); } }, l.toUpperCase())))) : null);
    if (!s) { mount(els.script, head, h('div', { class: 'empty' }, ui.scriptLang === 'en' ? 'No topic.md yet. Paste it in Intake.' : 'No topic.zh.md yet.')); return; }
    const summary = ui.scriptLang === 'en' ? h('div', { class: 'panel-body script-summary' },
      h('span', {}, h('strong', {}, s.words), ' words'),
      h('span', { class: 'muted' }, `target ${s.target_words} (${s.range[0]}–${s.range[1]}) at ${s.words_per_minute} wpm`),
      s.words_ok === false ? h('span', { class: 'chip error' }, 'outside tolerance') : h('span', { class: 'chip ok' }, 'within tolerance'),
      h('span', { class: 'muted small' }, `per slide ${s.narration_limits[0]}–${s.narration_limits[1]}`)) : null;
    const slides = s.slides.map(sl => h('div', { class: 'sslide', id: `script-${sl.index}` },
      h('div', { class: 'shead' }, h('span', { class: 'n' }, sl.index), h('span', { class: 'muted small' }, `line ${sl.line}`)),
      h('div', { class: 'content', html: sl.content_html }),
      ui.scriptLang === 'en' ? h('div', { class: 'narration' },
        h('span', { class: `wc chip ${sl.words_ok === false ? 'error' : ''}`, title: `running total ${sl.running_words}` },
          `${sl.words} w · Σ ${sl.running_words}`),
        sl.narration || h('span', { class: 'muted' }, '(no narration)')) : null));
    mount(els.script, head, summary, h('div', { class: 'pane-scroll' }, slides));
  }

  // -- slides -----------------------------------------------------------------------------
  function renderSlides() {
    const show = data.show.results?.[0];
    const r = show?.render || {};
    const en = r.en?.slides || [], zh = r.zh?.slides || [];
    const n = Math.max(en.length, zh.length);
    const head = h('div', { class: 'panel-head' }, h('h2', {}, 'Slides'),
      h('span', { class: 'muted small' }, n ? `${en.length} EN${zh.length ? ` · ${zh.length} ZH` : ''} · click to enlarge` : ''));
    const intro = bumperRow('intro'), outro = bumperRow('outro');
    if (!n) { mount(els.slides, head, h('div', { class: 'pane-scroll' }, intro, h('div', { class: 'empty' }, 'Not rendered yet.'), outro)); return; }
    const thumb = (lang, i) => {
      const src = r[lang]?.slides?.[i];
      const flags = r[lang]?.flagged?.[String(i + 1)] || [];
      const lvl = flags.some(f => f.level === 'error') ? 'flagged' : flags.length ? 'flagged-warn' : '';
      if (!src) return h('div', { class: 'thumb missing' }, `no ${lang} slide ${i + 1}`);
      return h('div', { class: `thumb ${lvl}`, title: flags.map(f => f.message).join('\n'), onclick: () => lightbox(i) },
        h('img', { src: `/files/${src}?v=${stamp(src)}`, alt: `${lang.toUpperCase()} slide ${i + 1}`, loading: 'lazy' }),
        h('span', { class: 'i' }, `${lang.toUpperCase()} ${i + 1}`),
        flags.length ? h('span', { class: `flag chip ${lvl === 'flagged' ? 'error' : 'warn'}` }, 'overflow') : null);
    };
    // One row per slide, scrolling vertically like the script; Mandarin beside English at the same index.
    const rows = [];
    for (let i = 0; i < n; i++) {
      rows.push(h('div', { class: 'sslide slide-row', id: `slide-${i + 1}` },
        h('div', { class: 'shead' }, h('span', { class: 'n' }, i + 1),
          h('span', { class: 'muted small' }, show.en?.slides?.[i]?.title || '')),
        h('div', { class: `slide-pair${zh.length ? ' two' : ''}` }, thumb('en', i), zh.length ? thumb('zh', i) : null)));
    }
    // In running order: the intro, the slides, then the outro.
    mount(els.slides, head, h('div', { class: 'pane-scroll' }, intro, rows, outro));
  }

  // A bumper row from bcn bumpers, per language: the intro (the title card) or the outro
  // (the logo card), each with its own still and video.
  function bumperRow(kind) {
    const arts = (data.status?.artifacts || []).filter(a => a.kind.startsWith('bumper') && a.exists);
    const cardOf = (a) => a.kind === 'bumper_card' && a.path.includes(`.${kind}-card.`);
    const langs = ['en', 'zh'].filter(l => arts.some(a => a.lang === l && cardOf(a)));
    if (!langs.length) return null;
    const card = (lang) => {
      const mine = arts.filter(a => a.lang === lang);
      const img = mine.find(cardOf);
      const video = mine.find(a => a.path.endsWith(`.${kind}.${lang}.mp4`));
      return h('div', {},
        h('div', { class: 'thumb' }, h('img', { src: `/files/${img.path}?v=${stamp(img.path)}`, alt: `${lang.toUpperCase()} ${kind} title card`, loading: 'lazy' }),
          h('span', { class: 'i' }, lang.toUpperCase()),
          (img.stale || mine.find(a => a.path.endsWith(`.${kind}.${lang}.mp4`))?.stale) ? h('span', { class: 'flag chip stale' }, 'out of date') : null),
        video ? h('div', { class: 'small row' }, h('a', { href: `/files/${video.path}?v=${stamp(video.path)}`, target: '_blank' }, `▶ play ${kind}`)) : null);
    };
    return h('div', { class: 'sslide slide-row', id: `slide-${kind}` },
      h('div', { class: 'shead' }, h('span', { class: 'n' }, '◆'), h('span', { class: 'muted small' }, kind === 'intro' ? 'Intro · title' : 'Outro · logo')),
      h('div', { class: `slide-pair${langs.length > 1 ? ' two' : ''}` }, langs.map(card)));
  }

  function lightbox(start) {
    const show = data.show.results?.[0];
    const r = show.render;
    const theme = r.theme;
    const n = Math.max(r.en?.slides?.length || 0, r.zh?.slides?.length || 0);
    let i = start;
    const root = document.getElementById('modal-root');
    const close = () => { root.replaceChildren(); document.removeEventListener('keydown', onKey); };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowRight') { i = Math.min(n - 1, i + 1); draw(); }
      else if (e.key === 'ArrowLeft') { i = Math.max(0, i - 1); draw(); }
      else if (e.key.toLowerCase() === 's') { ui.safeArea = !ui.safeArea; draw(); }
      else return;
      e.preventDefault();
    };
    document.addEventListener('keydown', onKey);
    root.addEventListener('route', close, { once: true });
    const frame = (lang) => {
      const src = r[lang]?.slides?.[i];
      if (!src) return null;
      const pct = theme ? (100 * theme.safe_bottom) / theme.height : 20;
      return h('figure', {},
        h('img', { src: `/files/${src}?v=${stamp(src)}`, alt: `${lang} slide ${i + 1}` }),
        ui.safeArea ? h('div', { class: 'safe-area', style: { height: `${pct}%` } }, h('span', {}, `subtitle safe area · ${theme?.safe_bottom}px`)) : null,
        h('figcaption', {}, `${lang === 'en' ? 'English' : 'Mandarin'} · slide ${i + 1}`));
    };
    const draw = () => {
      const frames = [frame('en'), frame('zh')].filter(Boolean);
      const msgs = ['en', 'zh'].flatMap(l => (r[l]?.flagged?.[String(i + 1)] || []).map(f => `${l.toUpperCase()}: ${f.message}`));
      mount(root, h('div', { class: 'lightbox', role: 'dialog', 'aria-modal': 'true', onclick: (e) => { if (e.target === e.currentTarget) close(); } },
        h('div', { class: `frames${frames.length > 1 ? ' two' : ''}` }, frames),
        msgs.length ? h('div', { class: 'msg' }, msgs.map(m => h('div', {}, m))) : null,
        h('div', { class: 'bar' },
          h('button', { class: 'btn', onclick: () => { i = Math.max(0, i - 1); draw(); } }, '← prev'),
          h('span', {}, `${i + 1} / ${n}`),
          h('button', { class: 'btn', onclick: () => { i = Math.min(n - 1, i + 1); draw(); } }, 'next →'),
          h('button', { class: 'btn', onclick: () => { ui.safeArea = !ui.safeArea; draw(); } }, ui.safeArea ? 'Hide safe area' : 'Show safe area'),
          h('span', { class: 'muted small' }, h('span', { class: 'kbd' }, '←'), ' ', h('span', { class: 'kbd' }, '→'), ' navigate · ',
            h('span', { class: 'kbd' }, 'S'), ' safe area · ', h('span', { class: 'kbd' }, 'Esc'), ' close'),
          h('button', { class: 'btn', onclick: close }, 'Close'))));
    };
    draw();
  }

  // -- video --------------------------------------------------------------------------------
  const player = (() => {
    const video = h('video', { controls: true, preload: 'metadata', playsinline: true });
    const timeline = h('div', { class: 'timeline', title: 'Click to seek; markers are slide cues' });
    const played = h('div', { class: 'played' });
    const headEl = h('div', { class: 'head' });
    const slideBox = h('div', { class: 'current-slide' });
    const cueTable = h('tbody');
    const controls = h('div', { class: 'controls' });
    let key = '';           // which media is loaded
    let cues = [];
    let lastSlide = -1;
    let seeked = false;

    const offset = () => {
      const m = data?.show.results?.[0]?.media;
      return ui.source === 'master' ? 0 : (m?.draft_offset?.[ui.source.split('-')[1]] || 0);
    };
    const currentIndex = () => {
      const t = video.currentTime - offset();
      let idx = -1;
      cues.forEach((c, i) => { if (c.time <= t + 0.001) idx = i; });
      return idx;
    };
    const seekCue = (i) => {
      if (i < 0 || i >= cues.length) return;
      video.currentTime = cues[i].time + offset() + 0.01;
    };

    function sourceUrl(media) {
      if (ui.source === 'master') return media.master;
      return media.draft[ui.source.split('-')[1]];
    }

    function tick() {
      const d = video.duration || 0;
      const pct = d ? (100 * video.currentTime) / d : 0;
      played.style.width = `${pct}%`;
      headEl.style.left = `${pct}%`;
      const idx = currentIndex();
      if (idx !== lastSlide) {
        lastSlide = idx;
        drawSlide(idx);
        [...cueTable.children].forEach((tr, i) => tr.classList.toggle('current', i === idx));
        [...timeline.querySelectorAll('.mark')].forEach((m, i) => m.classList.toggle('current', i === idx));
      }
    }

    function drawSlide(idx) {
      const r = data?.show.results?.[0]?.render;
      const lang = ui.subs === 'zh' && r?.zh?.slides?.length ? 'zh' : 'en';
      const src = idx >= 0 ? r?.[lang]?.slides?.[idx] : null;
      mount(slideBox, src ? h('img', { src: `/files/${src}?v=${stamp(src)}`, alt: `slide ${idx + 1}` }) : h('div', { class: 'thumb missing' }, 'no slide'),
        h('div', { class: 'cap' }, idx >= 0 ? `Slide ${idx + 1} (${lang.toUpperCase()}) from ${fmtTime(cues[idx].time)}` : '—'));
    }

    function drawMarks() {
      timeline.querySelectorAll('.mark').forEach(m => m.remove());
      const d = video.duration;
      if (!d) return;
      const show = data.show.results[0];
      cues.forEach((c, i) => {
        const low = c.source !== 'manual' && c.confidence !== null && c.confidence < show.cue_threshold;
        const left = (100 * (c.time + offset())) / d;
        timeline.append(h('button', {
          class: `mark${low ? ' low' : ''}${c.source === 'manual' ? ' manual' : ''}`, style: { left: `${left}%` },
          title: `Slide ${c.slide} at ${fmtTime(c.time)}${c.confidence !== null ? ` · confidence ${c.confidence.toFixed(2)}` : ''}${c.source === 'manual' ? ' · set by hand' : ''}${c.reason ? '\n' + c.reason : ''}`,
          onclick: (e) => { e.stopPropagation(); seekCue(i); } }, h('span', {}, c.slide)));
      });
    }

    function drawControls() {
      const media = data.show.results[0].media;
      const opts = [['master', 'Master', media.master], ['draft-en', 'Draft EN', media.draft.en], ['draft-zh', 'Draft ZH', media.draft.zh]];
      mount(controls,
        h('span', { class: 'seg' }, opts.map(([k, label, ok]) => h('button', { class: ui.source === k ? 'on' : '', disabled: !ok,
          onclick: () => { ui.source = k; update(); } }, label))),
        h('span', { class: 'muted' }, 'Subtitles'),
        h('span', { class: 'seg' }, [['en', 'EN'], ['zh', 'ZH'], ['off', 'Off']].map(([k, l]) => h('button', {
          class: ui.subs === k ? 'on' : '', disabled: k !== 'off' && !media.subtitles[k],
          onclick: () => { ui.subs = k; applySubs(); drawControls(); lastSlide = -2; tick(); } }, l))),
        h('span', { class: 'muted' }, 'Speed'),
        h('span', { class: 'seg' }, [1, 1.5, 2].map(r => h('button', { class: video.playbackRate === r ? 'on' : '',
          onclick: () => { video.playbackRate = r; drawControls(); } }, `${r}×`))),
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn small', onclick: () => seekCue(currentIndex() - (video.currentTime - offset() - (cues[currentIndex()]?.time || 0) < 0.6 ? 1 : 0)) }, '⟨ cue'),
        h('button', { class: 'btn small', onclick: () => seekCue(currentIndex() + 1) }, 'cue ⟩'),
        h('span', { class: 'muted small' }, h('span', { class: 'kbd' }, '['), ' ', h('span', { class: 'kbd' }, ']'), ' step cues'));
    }

    function applySubs() {
      for (const t of video.textTracks) t.mode = t.language === ui.subs ? 'showing' : 'disabled';
    }

    function drawCueTable() {
      const show = data.show.results[0];
      mount(cueTable, cues.map((c, i) => {
        const low = c.source !== 'manual' && c.confidence !== null && c.confidence < show.cue_threshold;
        return h('tr', { class: low ? 'low' : '' },
          h('td', {}, h('a', { href: '#', onclick: (e) => { e.preventDefault(); seekCue(i); } }, `Slide ${c.slide}`)),
          h('td', { class: 'mono' }, fmtTC(c.time)),
          h('td', { class: 'conf num' }, c.source === 'manual' ? 'manual' : c.confidence !== null ? c.confidence.toFixed(2) : '—'),
          h('td', { class: 'muted small' }, c.reason || ''),
          h('td', {}, c.slide > 1 ? h('button', { class: 'btn small', title: 'Record the playhead as this slide\'s start, in review.json',
            onclick: async () => {
              const t = Math.max(0, video.currentTime - offset());
              if (await confirmModal(`Set slide ${c.slide} to ${fmtTC(t)}?`, 'The boundary is recorded by hand in review.json and the cue sheet is regenerated. Watch the draft again afterwards.', 'Set'))
                runJob('cues', [rel], { set: `${c.slide}=${fmtTC(t)}` });
            } }, 'set to playhead') : null,
            c.source === 'manual' ? h('button', { class: 'btn small', onclick: () => runJob('cues', [rel], { unset: String(c.slide) }) }, 'unset') : null));
      }));
    }

    function update() {
      if (!data) return;
      const show = data.show.results?.[0];
      const media = show?.media;
      const head = h('div', { class: 'panel-head' }, h('h2', {}, 'Video'), h('span', { class: 'muted small' }, 'Cue markers from the cue sheet'));
      if (!media?.master) {
        mount(els.video, head, h('div', { class: 'empty' }, 'No edited video yet (edit/master.mp4).'));
        key = '';
        return;
      }
      if (ui.source !== 'master' && !sourceUrl(media)) ui.source = 'master';
      cues = show.cues || [];
      const url = sourceUrl(media);
      const k = [url, media.subtitles.en, media.subtitles.zh].map(p => `${p}@${p ? stamp(p) : ''}`).join('|');
      if (k !== key) {
        const t = video.currentTime || 0;
        key = k;
        video.replaceChildren();
        video.src = `/files/${url}?v=${stamp(url)}`;
        for (const lang of ['en', 'zh']) {
          if (media.subtitles[lang]) video.append(h('track', { kind: 'subtitles', srclang: lang, label: lang === 'en' ? 'English' : '中文',
            src: `/files/${media.subtitles[lang]}?format=vtt&v=${stamp(media.subtitles[lang])}` }));
        }
        video.addEventListener('loadedmetadata', () => {
          applySubs(); drawMarks();
          if (!seeked && startAt !== null && startAt !== undefined) { video.currentTime = startAt + offset(); seeked = true; }
          else if (t) video.currentTime = t;
          tick();
        }, { once: true });
      } else {
        drawMarks();
      }
      drawControls(); drawCueTable();
      lastSlide = -2; tick();
      if (!els.video.contains(video)) {
        timeline.append(played, headEl);
        mount(els.video, head, h('div', { class: 'player' },
          h('div', { class: 'player-main' }, video, slideBox), timeline, controls,
          h('table', { class: 'data cues', style: { marginTop: '10px' } },
            h('thead', {}, h('tr', {}, h('th', {}, 'Cue'), h('th', {}, 'Time'), h('th', { class: 'num' }, 'Conf.'), h('th', {}, 'Note'), h('th', {}))), cueTable)));
      }
    }

    video.addEventListener('timeupdate', tick);
    video.addEventListener('seeked', tick);
    video.addEventListener('ratechange', () => data && drawControls());
    timeline.addEventListener('click', (e) => {
      const r = timeline.getBoundingClientRect();
      if (video.duration) video.currentTime = ((e.clientX - r.left) / r.width) * video.duration;
    });
    const onKey = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
      if (document.getElementById('modal-root').childElementCount) return;
      if (e.key === ']') { seekCue(currentIndex() + 1); e.preventDefault(); }
      else if (e.key === '[') {
        const i = currentIndex();
        const into = video.currentTime - offset() - (cues[i]?.time || 0);
        seekCue(into > 0.6 ? i : i - 1); e.preventDefault();
      }
    };
    document.addEventListener('keydown', onKey);
    return { update, dispose: () => { document.removeEventListener('keydown', onKey); video.pause(); video.removeAttribute('src'); video.load(); } };
  })();

  // -- artefacts and diagnostics -----------------------------------------------------------
  function renderArtefacts() {
    const st = data?.status;
    const verified = new Map();
    for (const a of verify?.results?.[0]?.artifacts || []) verified.set(a.key, a.verified);
    const rows = (st?.artifacts || []).filter(a => a.exists || a.kind === 'source');
    mount(els.artefacts,
      h('div', { class: 'panel-head' }, h('h2', {}, 'Artefacts'),
        verify ? h('span', { class: `chip ${verify.results?.[0]?.ok ? 'ok' : 'error'}` }, verify.results?.[0]?.ok ? 'verified' : 'verify found problems')
          : h('span', { class: 'muted small' }, 'verifying…')),
      h('table', { class: 'data artefacts' },
        h('thead', {}, h('tr', {}, ['File', 'Lang', 'Modified', 'Size', 'State'].map(x => h('th', {}, x)))),
        h('tbody', {}, rows.map(a => h('tr', {},
          h('td', { class: 'path' }, a.exists ? h('a', { href: `/files/${a.path}`, target: '_blank' }, a.key) : h('span', { class: 'muted' }, a.key),
            a.count ? h('span', { class: 'muted' }, ` (${a.count})`) : null),
          h('td', {}, a.lang ? a.lang.toUpperCase() : ''),
          h('td', { class: 'small' }, a.exists ? fmtAgo(a.mtime) : h('span', { class: 'muted' }, 'missing')),
          h('td', { class: 'small num' }, fmtBytes(a.bytes)),
          h('td', {}, h('span', { class: 'row' },
            a.stale ? h('span', { class: 'chip stale' }, 'stale') : null,
            a.hydration === 'cloud' ? h('span', { class: 'chip cloud' }, '☁ cloud-only') : null,
            verified.get(a.key) === true ? h('span', { class: 'chip ok' }, 'ok') : null,
            verified.get(a.key) === false ? h('span', { class: 'chip error' }, 'broken') : null)))))));
  }

  function renderDiags() {
    const all = [...(verify?.diagnostics || []), ...diags];
    const order = { error: 0, warn: 1, info: 2 };
    all.sort((a, b) => order[a.level] - order[b.level]);
    mount(els.diags,
      h('div', { class: 'panel-head' }, h('h2', {}, 'Diagnostics'),
        h('a', { class: 'small', href: '#/diagnostics/mistranscriptions' }, 'Proofread mis-transcriptions →')),
      all.length ? h('ul', { class: 'diaglist' }, all.map(d => h('li', {},
        h('div', {}, levelChip(d.level)),
        h('div', {},
          h('div', {}, d.message),
          h('div', { class: 'loc' }, [d.code, d.lang?.toUpperCase(), d.file ? `${d.file}${d.line ? ':' + d.line : ''}` : null,
            d.slide ? `slide ${d.slide}` : null, d.data?.step ? `from ${d.data.step}` : null].filter(Boolean).join(' · ')),
          d.hint ? h('div', { class: 'hint' }, d.hint) : null,
          (d.file === 'topic.md' || d.file === 'topic.zh.md') ? h('a', { class: 'small',
            href: `#/edit/${id}?lang=${d.file === 'topic.zh.md' ? 'zh' : 'en'}${d.line ? '&line=' + d.line : ''}` },
            d.line ? `edit line ${d.line}` : 'edit script') : null,
          ackControls(d),
          d.data?.time !== undefined && d.data?.time !== null ? h('a', { class: 'small', href: '#', onclick: (e) => {
            e.preventDefault(); location.hash = `#/topic/${id}?t=${d.data.time}`; } }, `play at ${fmtTime(d.data.time)}`) : null))))
        : h('div', { class: 'empty' }, 'Nothing outstanding.'));
  }

  // -- lifecycle -----------------------------------------------------------------------------
  load().then(runVerify);
  let reloadTimer = null;
  const off = subscribe((kind) => {
    if (kind === 'status') {
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(load, 200);
    }
  });
  return { dispose: () => { disposed = true; off(); player.dispose(); } };
}
