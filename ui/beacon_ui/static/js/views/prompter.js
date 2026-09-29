// Teleprompter: the topic's narration, full screen, scrolling past a reading line.
// Reads the same parsed script as the Script pane (bcn show), so it is always current.
//
// Keys: Space play/pause · ↑/↓ speed · +/− text size · ←/→ or PageUp/PageDown previous/next
// slide (what most presentation clickers send) · Home back to the top · M mirror · F full screen
// · Esc leave.
import { api, h, mount } from '../core.js';

const PREFS_KEY = 'beacon-prompter';
const DEFAULTS = { speed: 5, size: 56, width: 70, mirror: false, countdown: true };

function loadPrefs() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}
function savePrefs(p) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch { /* private window: fine */ }
}

// Narration may carry **bold** and *emphasis*; everything else is shown as written.
function inline(text) {
  const esc = text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?![*\w])/g, '$1<em>$2</em>');
}

function clock(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function prompterView(main, id) {
  const prefs = loadPrefs();
  let slides = [];
  let pos = 0;              // scroll position in px, fractional
  let playing = false;
  let raf = null;
  let last = 0;
  let elapsed = 0;          // ms spent playing
  let countdownTimer = null;
  let hideTimer = null;

  document.body.classList.add('prompting');
  const stage = h('div', { class: 'prompter', tabindex: '-1' });
  const text = h('div', { class: 'prompter-text' });
  const eyeline = h('div', { class: 'prompter-eyeline' }, h('span', {}, '▶'), h('span', {}, '◀'));
  const overlay = h('div', { class: 'prompter-countdown hidden' });
  const bar = h('div', { class: 'prompter-bar' });
  stage.append(text);
  mount(main, stage, eyeline, overlay, bar);

  function apply() {
    text.style.fontSize = `${prefs.size}px`;
    text.style.width = `${prefs.width}vw`;
    text.style.transform = prefs.mirror ? 'scaleX(-1)' : '';
    savePrefs(prefs);
    drawBar();
  }

  // Pixels per second scale with the text, so a speed setting feels the same at any size.
  // Speed 5 at the default size and width is roughly 145 words a minute.
  const pxPerSec = () => prefs.speed * prefs.size * 0.085;

  function tick(now) {
    const dt = last ? (now - last) / 1000 : 0;
    last = now;
    if (playing) {
      pos += pxPerSec() * dt;
      const max = stage.scrollHeight - stage.clientHeight;
      if (pos >= max) { pos = max; setPlaying(false); }
      stage.scrollTop = pos;
      elapsed += dt * 1000;
    }
    drawStatus();
    raf = requestAnimationFrame(tick);
  }

  function setPlaying(on) {
    clearInterval(countdownTimer);
    overlay.classList.add('hidden');
    if (on && prefs.countdown && stage.scrollTop < 5) {
      let n = 3;
      overlay.textContent = n;
      overlay.classList.remove('hidden');
      countdownTimer = setInterval(() => {
        n -= 1;
        if (n > 0) { overlay.textContent = n; return; }
        clearInterval(countdownTimer);
        overlay.classList.add('hidden');
        playing = true; last = 0; drawBar(); wake();
      }, 800);
      return;
    }
    playing = on;
    last = 0;
    drawBar();
    wake();
  }

  // Keep manual scrolling (trackpad, wheel) and auto-scroll in step.
  stage.addEventListener('scroll', () => {
    if (Math.abs(stage.scrollTop - pos) > 2) pos = stage.scrollTop;
  });

  const dividers = () => [...text.querySelectorAll('.prompter-divider')];
  const eyeY = () => stage.clientHeight * 0.33;
  function currentSlide() {
    let cur = 0;
    dividers().forEach((d, i) => { if (d.offsetTop - stage.scrollTop <= eyeY() + 4) cur = i; });
    return cur;
  }
  function goToSlide(i) {
    const ds = dividers();
    if (!ds.length) return;
    const k = Math.max(0, Math.min(ds.length - 1, i));
    pos = Math.max(0, ds[k].offsetTop - eyeY());
    stage.scrollTop = pos;
    wake();
  }

  function drawStatus() {
    const st = bar.querySelector('.status');
    if (!st) return;
    const n = slides.length ? currentSlide() + 1 : 0;
    st.textContent = `slide ${n} of ${slides.length} · ${clock(elapsed)}`;
  }

  function btn(label, title, fn, on = false) {
    return h('button', { class: `pbtn${on ? ' on' : ''}`, title, onclick: (e) => { e.stopPropagation(); fn(); stage.focus(); } }, label);
  }

  function drawBar() {
    mount(bar,
      btn(playing ? '❚❚ Pause' : '▶ Play', 'Space', () => setPlaying(!playing), playing),
      h('span', { class: 'grp' }, 'Speed', btn('−', '↓', () => { prefs.speed = Math.max(1, prefs.speed - 1); apply(); }),
        h('b', {}, prefs.speed), btn('+', '↑', () => { prefs.speed = Math.min(30, prefs.speed + 1); apply(); })),
      h('span', { class: 'grp' }, 'Size', btn('−', '−', () => { prefs.size = Math.max(24, prefs.size - 4); apply(); }),
        h('b', {}, prefs.size), btn('+', '+', () => { prefs.size = Math.min(140, prefs.size + 4); apply(); })),
      h('span', { class: 'grp' }, 'Width', btn('−', '', () => { prefs.width = Math.max(40, prefs.width - 5); apply(); }),
        h('b', {}, `${prefs.width}%`), btn('+', '', () => { prefs.width = Math.min(95, prefs.width + 5); apply(); })),
      h('span', { class: 'grp' }, btn('⟨ Slide', '← or PageUp', () => goToSlide(currentSlide() - 1)),
        btn('Slide ⟩', '→ or PageDown', () => goToSlide(currentSlide() + 1))),
      btn('Mirror', 'M', () => { prefs.mirror = !prefs.mirror; apply(); }, prefs.mirror),
      btn('3-2-1', 'Countdown when starting from the top', () => { prefs.countdown = !prefs.countdown; apply(); }, prefs.countdown),
      btn('Full screen', 'F', toggleFullscreen),
      h('span', { class: 'status' }),
      btn('✕ Close', 'Esc', leave));
    drawStatus();
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.().catch(() => {});
  }

  function leave() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    location.hash = `#/topic/${id}`;
  }

  // Controls fade while playing and come back on any mouse movement.
  function wake() {
    bar.classList.remove('faded');
    clearTimeout(hideTimer);
    if (playing) hideTimer = setTimeout(() => bar.classList.add('faded'), 2500);
  }

  const onKey = (e) => {
    const k = e.key;
    if (k === ' ' || k === 'b' || k === 'B' || k === '.') setPlaying(!playing);
    else if (k === 'ArrowUp') { prefs.speed = Math.min(30, prefs.speed + 1); apply(); }
    else if (k === 'ArrowDown') { prefs.speed = Math.max(1, prefs.speed - 1); apply(); }
    else if (k === 'ArrowRight' || k === 'PageDown') goToSlide(currentSlide() + 1);
    else if (k === 'ArrowLeft' || k === 'PageUp') goToSlide(currentSlide() - 1);
    else if (k === '+' || k === '=') { prefs.size = Math.min(140, prefs.size + 4); apply(); }
    else if (k === '-' || k === '_') { prefs.size = Math.max(24, prefs.size - 4); apply(); }
    else if (k === 'Home') { pos = 0; stage.scrollTop = 0; elapsed = 0; }
    else if (k === 'm' || k === 'M') { prefs.mirror = !prefs.mirror; apply(); }
    else if (k === 'f' || k === 'F') toggleFullscreen();
    else if (k === 'Escape' && !document.fullscreenElement) leave();
    else return;
    e.preventDefault();
    wake();
  };
  document.addEventListener('keydown', onKey);
  document.addEventListener('mousemove', wake);

  (async () => {
    let show;
    try {
      const data = await api(`/api/topic/${id}`);
      show = data.show?.results?.[0];
    } catch (e) {
      mount(text, h('p', {}, `Could not load the script: ${e.message}`));
      return;
    }
    const en = show?.en;
    if (!en) { mount(text, h('p', {}, 'This topic has no topic.md yet.')); return; }
    slides = en.slides;
    mount(text,
      h('div', { class: 'prompter-title' }, `${id} · ${en.front?.title || ''}`),
      slides.map(s => [
        h('div', { class: 'prompter-divider' }, `Slide ${s.index}${s.title ? ' · ' + s.title : ''}`),
        (s.paragraphs?.length ? s.paragraphs : [s.narration || '']).map(p => h('p', { html: inline(p) })),
      ]),
      h('div', { class: 'prompter-end' }, '— end —'));
    apply();
    stage.focus();
    raf = requestAnimationFrame(tick);
  })();
  apply();

  return {
    dispose() {
      cancelAnimationFrame(raf);
      clearInterval(countdownTimer);
      clearTimeout(hideTimer);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousemove', wake);
      document.body.classList.remove('prompting');
      if (document.fullscreenElement) document.exitFullscreen?.();
    },
  };
}
