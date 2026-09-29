<script setup lang="ts">
// The edited video (or a draft) with the cue sheet on its timeline. This is how a mistimed
// cue gets found: ten seconds of watching beats any report.
// Keys: [ and ] step between cues.
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ShowCue } from '@beacon/shared';
import { fileUrl } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtTC, fmtTime } from '@/format';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

const props = defineProps<{ startAt: number | null }>();
const t = inject(TOPIC)!;
const beacon = useBeacon();
const video = ref<HTMLVideoElement | null>(null);
const source = ref<'master' | 'draft-en' | 'draft-zh'>('master');
const subs = ref<'en' | 'zh' | 'off'>('en');
const rate = ref(1);
const time = ref(0);
const duration = ref(0);
let seeked = false;
let resumeAt = 0;

const show = computed(() => t.show.value);
const media = computed(() => show.value?.media ?? null);
const cues = computed<ShowCue[]>(() => show.value?.cues || []);
const threshold = computed(() => show.value?.cue_threshold ?? 0);
const draftLang = computed(() => (source.value === 'master' ? null : (source.value.split('-')[1] as 'en' | 'zh')));
const offset = computed(() => (draftLang.value ? media.value?.draft_offset?.[draftLang.value] || 0 : 0));
const url = computed(() => {
  const m = media.value;
  if (!m?.master) return null;
  const p = draftLang.value ? m.draft[draftLang.value] : m.master;
  return p ?? m.master;
});
const src = computed(() => (url.value ? fileUrl(url.value, t.stamp(url.value)) : ''));
const tracks = computed(() => (['en', 'zh'] as const).filter((l) => media.value?.subtitles[l]).map((lang) => ({
  lang, label: lang === 'en' ? 'English' : '中文', src: fileUrl(media.value!.subtitles[lang]!, t.stamp(media.value!.subtitles[lang]!), 'format=vtt'),
})));
const sources = computed(() => [
  { label: 'Master', value: 'master', disable: !media.value?.master },
  { label: 'Draft EN', value: 'draft-en', disable: !media.value?.draft.en },
  { label: 'Draft ZH', value: 'draft-zh', disable: !media.value?.draft.zh },
]);

const current = computed(() => {
  const at = time.value - offset.value;
  let idx = -1;
  cues.value.forEach((c, i) => { if (c.time <= at + 0.001) idx = i; });
  return idx;
});
const isLow = (c: ShowCue) => c.source !== 'manual' && c.confidence !== null && c.confidence < threshold.value;
const playedPct = computed(() => (duration.value ? (100 * time.value) / duration.value : 0));
const markLeft = (c: ShowCue) => `${(100 * (c.time + offset.value)) / duration.value}%`;
const slideLang = computed(() => (subs.value === 'zh' && show.value?.render.zh?.slides?.length ? 'zh' : 'en'));
const currentSlide = computed(() => {
  const i = current.value;
  const path = i >= 0 ? show.value?.render[slideLang.value]?.slides?.[i] : null;
  return { path: path ? fileUrl(path, t.stamp(path)) : null, caption: i >= 0 ? `Slide ${i + 1} (${slideLang.value.toUpperCase()}) from ${fmtTime(cues.value[i].time)}` : '—' };
});

function seekCue(i: number) {
  if (!video.value || i < 0 || i >= cues.value.length) return;
  video.value.currentTime = cues.value[i].time + offset.value + 0.01;
}
function prevCue() {
  const i = current.value;
  const into = time.value - offset.value - (cues.value[i]?.time || 0);
  seekCue(into > 0.6 ? i : i - 1);
}
function seekTimeline(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  const r = el.getBoundingClientRect();
  if (video.value && duration.value) video.value.currentTime = ((e.clientX - r.left) / r.width) * duration.value;
}
function applySubs() {
  if (!video.value) return;
  for (const tr of Array.from(video.value.textTracks)) tr.mode = tr.language === subs.value ? 'showing' : 'disabled';
}
// Read from the event, not the ref: a last event can arrive while the pane is being torn down.
const onTime = (e: Event) => { time.value = (e.target as HTMLVideoElement).currentTime; };
const onRate = (e: Event) => { rate.value = (e.target as HTMLVideoElement).playbackRate; };
function onMeta() {
  const v = video.value!;
  duration.value = v.duration || 0;
  applySubs();
  if (!seeked && props.startAt !== null) { v.currentTime = props.startAt + offset.value; seeked = true; }
  else if (resumeAt) v.currentTime = resumeAt;
  resumeAt = 0;
}
// A new file (a rebuilt draft, say) loads where the old one was.
watch(src, () => { resumeAt = video.value?.currentTime || 0; }, { flush: 'pre' });
watch(subs, applySubs);
watch(rate, (r) => { if (video.value) video.value.playbackRate = r; });

async function setToPlayhead(c: ShowCue) {
  const at = Math.max(0, time.value - offset.value);
  const ok = await confirm({ title: `Set slide ${c.slide} to ${fmtTC(at)}?`, ok: 'Set',
    lines: ['The boundary is recorded by hand in review.json and the cue sheet is regenerated. Watch the draft again afterwards.'] });
  if (ok) await beacon.runJob('cues', [t.rel], { set: `${c.slide}=${fmtTC(at)}` });
}

function onKey(e: KeyboardEvent) {
  const tag = (document.activeElement as HTMLElement | null)?.tagName;
  if (tag && ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
  if (document.querySelector('.q-dialog')) return;
  if (e.key === ']') { seekCue(current.value + 1); e.preventDefault(); }
  else if (e.key === '[') { prevCue(); e.preventDefault(); }
}
onMounted(() => document.addEventListener('keydown', onKey));
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey);
  const v = video.value;
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
});
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center q-pb-sm">
      <div class="text-subtitle1 text-weight-medium">Video</div>
      <q-space />
      <span class="text-caption text-grey-7">Cue markers from the cue sheet</span>
    </q-card-section>
    <q-card-section v-if="!media?.master" class="text-grey-7">No edited video yet (edit/master.mp4).</q-card-section>
    <q-card-section v-else>
      <div class="player-main">
        <video ref="video" :src="src" controls preload="metadata" playsinline
          @loadedmetadata="onMeta" @timeupdate="onTime" @seeked="onTime" @ratechange="onRate">
          <track v-for="tr in tracks" :key="tr.src" kind="subtitles" :srclang="tr.lang" :label="tr.label" :src="tr.src">
        </video>
        <div class="current-slide">
          <img v-if="currentSlide.path" :src="currentSlide.path" :alt="currentSlide.caption">
          <div v-else class="thumb missing">no slide</div>
          <div class="text-caption text-grey-7 q-mt-xs">{{ currentSlide.caption }}</div>
        </div>
      </div>
      <div class="timeline" @click="seekTimeline">
        <div class="played" :style="{ width: `${playedPct}%` }"></div>
        <div class="head" :style="{ left: `${playedPct}%` }"></div>
        <template v-if="duration">
          <button v-for="(c, i) in cues" :key="i" type="button" :style="{ left: markLeft(c) }"
            :class="['mark', { low: isLow(c), manual: c.source === 'manual', current: i === current }]" @click.stop="seekCue(i)">
            {{ c.slide }}
            <q-tooltip>Slide {{ c.slide }} at {{ fmtTime(c.time) }}{{ c.confidence !== null ? ` · confidence ${c.confidence.toFixed(2)}` : '' }}{{ c.source === 'manual' ? ' · set by hand' : '' }}{{ c.reason ? `\n${c.reason}` : '' }}</q-tooltip>
          </button>
        </template>
      </div>
      <div class="row items-center gap-sm">
        <q-btn-toggle v-model="source" dense no-caps unelevated size="sm" toggle-color="primary" :options="sources" />
        <span class="text-caption text-grey-7">Subtitles</span>
        <q-btn-toggle v-model="subs" dense no-caps unelevated size="sm" toggle-color="primary"
          :options="[{ label: 'EN', value: 'en', disable: !media.subtitles.en }, { label: 'ZH', value: 'zh', disable: !media.subtitles.zh }, { label: 'Off', value: 'off' }]" />
        <span class="text-caption text-grey-7">Speed</span>
        <q-btn-toggle v-model="rate" dense no-caps unelevated size="sm" toggle-color="primary"
          :options="[{ label: '1×', value: 1 }, { label: '1.5×', value: 1.5 }, { label: '2×', value: 2 }]" />
        <q-space />
        <q-btn outline dense size="sm" no-caps label="⟨ cue" @click="prevCue" />
        <q-btn outline dense size="sm" no-caps label="cue ⟩" @click="seekCue(current + 1)" />
        <span class="text-caption text-grey-7">[ ] step cues</span>
      </div>
      <q-markup-table flat dense class="q-mt-md">
        <thead><tr><th class="text-left">Cue</th><th class="text-left">Time</th><th class="text-right">Conf.</th><th class="text-left">Note</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(c, i) in cues" :key="i" :class="{ low: isLow(c), current: i === current }">
            <td><a href="#" @click.prevent="seekCue(i)">Slide {{ c.slide }}</a></td>
            <td class="text-mono">{{ fmtTC(c.time) }}</td>
            <td class="conf text-right">{{ c.source === 'manual' ? 'manual' : c.confidence !== null ? c.confidence.toFixed(2) : '—' }}</td>
            <td class="text-caption text-grey-7">{{ c.reason || '' }}</td>
            <td class="q-gutter-xs text-right">
              <q-btn v-if="c.slide > 1" flat dense size="sm" no-caps label="Set to playhead" @click="setToPlayhead(c)">
                <q-tooltip>Record the playhead as this slide's start, in review.json</q-tooltip>
              </q-btn>
              <q-btn v-if="c.source === 'manual'" flat dense size="sm" no-caps label="Unset" @click="beacon.runJob('cues', [t.rel], { unset: String(c.slide) })" />
            </td>
          </tr>
        </tbody>
      </q-markup-table>
    </q-card-section>
  </q-card>
</template>
