<script setup lang="ts">
// Teleprompter: the topic's narration, full screen, scrolling past a reading line.
// Reads the same parsed script as the Script pane (bcn show), so it is always current.
//
// Keys: Space play/pause · ↑/↓ speed · +/− text size · ←/→ or PageUp/PageDown previous/next
// slide (what most presentation clickers send) · Home back to the top · M mirror · F full screen
// · Esc leave.
import { onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue';
import { useRouter } from 'vue-router';
import type { ShowScript, TopicResponse } from '@beacon/shared';
import { api } from '@/api';

const PREFS_KEY = 'beacon-prompter';
const DEFAULTS = { speed: 5, size: 56, width: 70, mirror: false, countdown: true };

const props = defineProps<{ id: string }>();
const router = useRouter();

function loadPrefs() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}
const prefs = reactive<typeof DEFAULTS>(loadPrefs());
function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private window: fine */ }
}

// Narration may carry **bold** and *emphasis*; everything else is shown as written.
function inline(text: string) {
  const esc = text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!));
  return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?![*\w])/g, '$1<em>$2</em>');
}
const clock = (ms: number) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

const script = shallowRef<ShowScript | null>(null);
const error = ref<string | null>(null);
const stage = ref<HTMLDivElement | null>(null);
const textEl = ref<HTMLDivElement | null>(null);
const playing = ref(false);
const countdown = ref<number | null>(null);
const faded = ref(false);
const statusLine = ref('');
let pos = 0;              // scroll position in px, fractional
let last = 0;
let elapsed = 0;          // ms spent playing
let raf = 0;
let countdownTimer: ReturnType<typeof setInterval> | undefined;
let hideTimer: ReturnType<typeof setTimeout> | undefined;

// Pixels per second scale with the text, so a speed setting feels the same at any size.
// Speed 5 at the default size and width is roughly 145 words a minute.
const pxPerSec = () => prefs.speed * prefs.size * 0.085;
const dividers = () => [...(textEl.value?.querySelectorAll<HTMLElement>('.prompter-divider') || [])];
const eyeY = () => (stage.value?.clientHeight || 0) * 0.33;

function currentSlide() {
  let cur = 0;
  dividers().forEach((d, i) => { if (d.offsetTop - (stage.value?.scrollTop || 0) <= eyeY() + 4) cur = i; });
  return cur;
}
function goToSlide(i: number) {
  const ds = dividers();
  if (!ds.length || !stage.value) return;
  const k = Math.max(0, Math.min(ds.length - 1, i));
  pos = Math.max(0, ds[k].offsetTop - eyeY());
  stage.value.scrollTop = pos;
  wake();
}

function tick(now: number) {
  const dt = last ? (now - last) / 1000 : 0;
  last = now;
  const st = stage.value;
  if (st && playing.value) {
    pos += pxPerSec() * dt;
    const max = st.scrollHeight - st.clientHeight;
    if (pos >= max) { pos = max; setPlaying(false); }
    st.scrollTop = pos;
    elapsed += dt * 1000;
  }
  const n = script.value?.slides.length || 0;
  statusLine.value = `slide ${n ? currentSlide() + 1 : 0} of ${n} · ${clock(elapsed)}`;
  raf = requestAnimationFrame(tick);
}

function setPlaying(on: boolean) {
  clearInterval(countdownTimer);
  countdown.value = null;
  if (on && prefs.countdown && (stage.value?.scrollTop || 0) < 5) {
    countdown.value = 3;
    countdownTimer = setInterval(() => {
      if (countdown.value! > 1) { countdown.value! -= 1; return; }
      clearInterval(countdownTimer);
      countdown.value = null;
      playing.value = true; last = 0; wake();
    }, 800);
    return;
  }
  playing.value = on;
  last = 0;
  wake();
}

// Keep manual scrolling (trackpad, wheel) and auto-scroll in step.
function onScroll() { if (stage.value && Math.abs(stage.value.scrollTop - pos) > 2) pos = stage.value.scrollTop; }

// Controls fade while playing and come back on any mouse movement.
function wake() {
  faded.value = false;
  clearTimeout(hideTimer);
  if (playing.value) hideTimer = setTimeout(() => { faded.value = true; }, 2500);
}

function change(key: 'speed' | 'size' | 'width', delta: number) {
  const limits = { speed: [1, 30], size: [24, 140], width: [40, 95] }[key];
  prefs[key] = Math.max(limits[0], Math.min(limits[1], prefs[key] + delta));
  savePrefs();
}
function toggle(key: 'mirror' | 'countdown') { prefs[key] = !prefs[key]; savePrefs(); }
function toggleFullscreen() {
  if (document.fullscreenElement) void document.exitFullscreen?.();
  else void document.documentElement.requestFullscreen?.().catch(() => {});
}
function leave() {
  if (document.fullscreenElement) void document.exitFullscreen?.();
  void router.push(`/topic/${props.id}`);
}

function onKey(e: KeyboardEvent) {
  const k = e.key;
  if (k === ' ' || k === 'b' || k === 'B' || k === '.') setPlaying(!playing.value);
  else if (k === 'ArrowUp') change('speed', 1);
  else if (k === 'ArrowDown') change('speed', -1);
  else if (k === 'ArrowRight' || k === 'PageDown') goToSlide(currentSlide() + 1);
  else if (k === 'ArrowLeft' || k === 'PageUp') goToSlide(currentSlide() - 1);
  else if (k === '+' || k === '=') change('size', 4);
  else if (k === '-' || k === '_') change('size', -4);
  else if (k === 'Home') { pos = 0; if (stage.value) stage.value.scrollTop = 0; elapsed = 0; }
  else if (k === 'm' || k === 'M') toggle('mirror');
  else if (k === 'f' || k === 'F') toggleFullscreen();
  else if (k === 'Escape' && !document.fullscreenElement) leave();
  else return;
  e.preventDefault();
  wake();
}

onMounted(async () => {
  document.body.classList.add('prompting');
  document.addEventListener('keydown', onKey);
  document.addEventListener('mousemove', wake);
  try {
    const data = await api<TopicResponse>(`/api/topic/${props.id}`);
    script.value = data.show.results?.[0]?.en ?? null;
    if (!script.value) error.value = 'This topic has no topic.md yet.';
  } catch (e) {
    error.value = `Could not load the script: ${(e as Error).message}`;
  }
  stage.value?.focus();
  raf = requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  clearInterval(countdownTimer);
  clearTimeout(hideTimer);
  document.removeEventListener('keydown', onKey);
  document.removeEventListener('mousemove', wake);
  document.body.classList.remove('prompting');
  if (document.fullscreenElement) void document.exitFullscreen?.();
});
</script>

<template>
  <div>
    <div ref="stage" class="prompter" tabindex="-1" @scroll="onScroll">
      <div ref="textEl" class="prompter-text"
        :style="{ fontSize: `${prefs.size}px`, width: `${prefs.width}vw`, transform: prefs.mirror ? 'scaleX(-1)' : '' }">
        <p v-if="error">{{ error }}</p>
        <template v-else-if="script">
          <div class="prompter-title">{{ id }} · {{ script.front?.title || '' }}</div>
          <template v-for="s in script.slides" :key="s.index">
            <div class="prompter-divider">Slide {{ s.index }}{{ s.title ? ` · ${s.title}` : '' }}</div>
            <!-- Escaped above; only ** and * become markup. -->
            <p v-for="(p, i) in (s.paragraphs?.length ? s.paragraphs : [s.narration || ''])" :key="i" v-html="inline(p)"></p>
          </template>
          <div class="prompter-end">— end —</div>
        </template>
      </div>
    </div>
    <div class="prompter-eyeline"><span>▶</span><span>◀</span></div>
    <div v-if="countdown !== null" class="prompter-countdown">{{ countdown }}</div>
    <div :class="['prompter-bar', { faded }]">
      <q-toolbar class="bg-grey-10 text-grey-4 q-gutter-sm" style="flex-wrap: wrap">
        <q-btn :color="playing ? 'amber-8' : 'grey-8'" unelevated no-caps :icon="playing ? 'pause' : 'play_arrow'" :label="playing ? 'Pause' : 'Play'" @click="setPlaying(!playing)"><q-tooltip>Space</q-tooltip></q-btn>
        <span v-for="k in (['speed', 'size', 'width'] as const)" :key="k" class="row items-center q-gutter-xs">
          <span class="text-caption text-capitalize">{{ k }}</span>
          <q-btn dense flat round icon="remove" @click="change(k, k === 'speed' ? -1 : k === 'size' ? -4 : -5)" />
          <b>{{ prefs[k] }}{{ k === 'width' ? '%' : '' }}</b>
          <q-btn dense flat round icon="add" @click="change(k, k === 'speed' ? 1 : k === 'size' ? 4 : 5)" />
        </span>
        <q-btn dense flat no-caps icon="skip_previous" label="Slide" @click="goToSlide(currentSlide() - 1)"><q-tooltip>← or PageUp</q-tooltip></q-btn>
        <q-btn dense flat no-caps icon-right="skip_next" label="Slide" @click="goToSlide(currentSlide() + 1)"><q-tooltip>→ or PageDown</q-tooltip></q-btn>
        <q-btn dense no-caps :flat="!prefs.mirror" :color="prefs.mirror ? 'amber-8' : undefined" label="Mirror" @click="toggle('mirror')"><q-tooltip>M</q-tooltip></q-btn>
        <q-btn dense no-caps :flat="!prefs.countdown" :color="prefs.countdown ? 'amber-8' : undefined" label="3-2-1" @click="toggle('countdown')"><q-tooltip>Countdown when starting from the top</q-tooltip></q-btn>
        <q-btn dense flat no-caps icon="fullscreen" label="Full screen" @click="toggleFullscreen"><q-tooltip>F</q-tooltip></q-btn>
        <q-space />
        <span class="text-caption">{{ statusLine }}</span>
        <q-btn dense flat no-caps icon="close" label="Close" @click="leave"><q-tooltip>Esc</q-tooltip></q-btn>
      </q-toolbar>
    </div>
  </div>
</template>
