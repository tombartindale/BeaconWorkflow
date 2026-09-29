<script setup lang="ts">
// The topic's pipeline in both languages: English steps on one row, Mandarin on the row below,
// branching off between English cues and subtitles. Each step shows whether bcn says it is done,
// out of date, failed or not run, with the next step marked. Clicking a step runs it in its
// row's language. Tools that are not steps sit below and use the language chosen at the top.
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { StepState } from '@beacon/shared';
import { fmtAgo, LANG_NAME, STEP_HELP } from '@/format';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

type Item =
  | { kind: 'step'; name: string; optional?: boolean }
  | { kind: 'wait'; name: string; label: string; icon: string };

// The order bcn works through (tooling/bcn/state.py). The waits are what a person does
// between steps: the editor delivers the recording, the translator returns the Mandarin.
const FLOW: Record<'en' | 'zh', Item[]> = {
  en: [
    { kind: 'step', name: 'validate' }, { kind: 'step', name: 'render' },
    { kind: 'wait', name: 'recording', label: 'Recording', icon: 'videocam' },
    { kind: 'step', name: 'cues' }, { kind: 'step', name: 'subtitles' },
    { kind: 'step', name: 'compose', optional: true }, { kind: 'step', name: 'package' },
  ],
  zh: [
    { kind: 'wait', name: 'translation', label: 'Translation', icon: 'translate' },
    { kind: 'step', name: 'validate' }, { kind: 'step', name: 'subtitles' }, { kind: 'step', name: 'render' },
    { kind: 'step', name: 'compose', optional: true }, { kind: 'step', name: 'package' },
  ],
};
const WAIT_HELP: Record<string, string> = {
  recording: 'The editor delivers edit/master.mp4 and its subtitles; they arrive by sync. Nothing to run here.',
  translation: 'The English SRT and slides go to the translator from the Translation page, and the returned files are imported there. '
    + 'A topic can be sent once its English subtitles step is done, so any corrected mishearings reach the translator.',
};

const LANGS = ['en', 'zh'] as const;

const props = defineProps<{ lang: 'en' | 'zh' }>();
const emit = defineEmits<{ verify: [] }>();
const t = inject(TOPIC)!;
const beacon = useBeacon();
const force = ref(false);
const noBumpers = ref(true);

const stateOf = (lang: 'en' | 'zh') => t.status.value?.[lang] ?? null;

function view(item: Item, lang: 'en' | 'zh') {
  const s = stateOf(lang);
  const next = s?.next ?? null;
  if (item.kind === 'wait') {
    const current = item.name === 'recording' ? next === 'await_recording'
      : next === 'translation_export' || next === 'await_translation';
    const reached = !!s && (item.name === 'recording'
      ? s.stage_index >= s.stages.indexOf('recorded')
      : s.stage_index >= s.stages.indexOf('returned'));
    const cap = current ? (next === 'translation_export' ? 'ready to send' : next === 'await_translation' ? 'with the translator' : 'waiting for the edit')
      : reached ? 'arrived' : item.name === 'translation' ? 'after English subtitles' : 'not yet';
    return { cls: ['wait', { current }], icon: reached && !current ? 'check_circle' : item.icon, color: current ? 'white' : reached ? 'positive' : undefined,
      cap, tip: WAIT_HELP[item.name] };
  }
  const st: StepState | undefined = s?.steps?.[item.name];
  const current = next === item.name;
  // Every Mandarin step reads topic.zh.md / the Mandarin SRT, which do not exist until
  // translation has been returned (tooling/bcn/state.py: validate/subtitles/render take
  // zsrc/zsrt as inputs). Running one before then would just fail, so it is disabled here.
  const waitingForTranslation = lang === 'zh' && !!s && s.stage_index < s.stages.indexOf('returned');
  const status = !st?.exists ? 'todo' : !st.ok ? 'failed' : st.fresh ? 'done' : 'stale';
  const ICON = { done: 'check_circle', stale: 'update', failed: 'error', todo: 'radio_button_unchecked' } as const;
  const COLOR = { done: 'positive', stale: 'warning', failed: 'negative', todo: 'grey-6' } as const;
  const CAP = { done: `done${st?.time ? ` · ${fmtAgo(st.time, beacon.now)}` : ''}`, stale: 'out of date', failed: 'failed', todo: item.optional ? 'optional check' : 'not run' };
  return {
    cls: ['step-btn', status, { current, optional: item.optional, disabled: waitingForTranslation }],
    icon: current && status === 'todo' ? 'play_circle' : ICON[status],
    color: current ? 'white' : COLOR[status],
    cap: waitingForTranslation ? 'needs translation' : current ? `next · ${CAP[status]}` : CAP[status],
    disable: waitingForTranslation,
    tip: `${lang === 'zh' ? 'Mandarin. ' : ''}${STEP_HELP[item.name]}${current ? ' This is the next step for this topic.' : ''}`
      + (waitingForTranslation ? ' Needs topic.zh.md and the Mandarin SRT back from the translator first.' : ''),
  };
}
const rows = computed(() => LANGS.map((lang) => ({
  lang, complete: !!stateOf(lang)?.complete,
  items: FLOW[lang].map((item) => ({ item, v: view(item, lang) })),
})));

// Two connectors drawn over the rows: English, after subtitles, down to the start of the
// Mandarin row (that is when a topic can go out for translation); and the end of the Mandarin
// row back up to English package, which delivers both languages together. Measured from the
// blocks themselves, so they follow the layout at any width.
const wrap = ref<HTMLElement | null>(null);
const links = ref<{ d: string; key: string }[]>([]);
const size = ref({ w: 0, h: 0 });
function measure() {
  const w = wrap.value;
  if (!w) return;
  const box = w.getBoundingClientRect();
  const at = (key: string) => {
    const el = w.querySelector<HTMLElement>(`[data-step="${key}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { l: r.left - box.left, r: r.right - box.left, t: r.top - box.top, b: r.bottom - box.top };
  };
  const subs = at('en-subtitles'), pkg = at('en-package');
  const first = at('zh-translation'), last = at('zh-package');
  size.value = { w: w.scrollWidth, h: w.scrollHeight };
  if (!subs || !pkg || !first || !last) { links.value = []; return; }
  const gapTop = Math.max(subs.b, pkg.b);
  const gapBottom = Math.min(first.t, last.t);
  const y1 = gapTop + (gapBottom - gapTop) * 0.35;   // two lanes, so the lines never overlap
  const y2 = gapTop + (gapBottom - gapTop) * 0.7;
  const out = subs.r + 11;                           // just after subtitles, in the arrow gap
  const inX = first.l + 26;
  const back = last.r + 11;
  const upX = pkg.l + (pkg.r - pkg.l) / 2;
  links.value = [
    { key: 'out', d: `M ${out} ${subs.b - 6} V ${y1} H ${inX} V ${first.t - 2}` },  // below the arrow glyph
    { key: 'back', d: `M ${last.r + 1} ${last.t + (last.b - last.t) / 2} H ${back} V ${y2} H ${upX} V ${pkg.b + 2}` },
  ];
}
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(() => measure());
  if (wrap.value) ro.observe(wrap.value);
  nextTick(measure);
});
onBeforeUnmount(() => ro?.disconnect());
watch(rows, () => nextTick(measure));

async function run(step: string, lang: 'en' | 'zh') {
  const args: Record<string, string | boolean> = { lang: step === 'cues' ? 'en' : lang };
  if (force.value) args.force = true;
  if (step === 'compose' && noBumpers.value) args.no_bumpers = true;
  if (step === 'qa') delete args.lang;
  await beacon.runJob(step, [t.rel], args);
}

// These two make files the page links to, so it waits for them and says where they are.
async function runAndReport(command: string, args: Record<string, string | boolean>, done: string, failed: string) {
  const job = await beacon.runJob(command, [t.rel], args);
  const result = await beacon.awaitJob(job.id);
  await t.reload();
  if (result.state === 'done') beacon.toast(done); else beacon.toast(failed, true);
}
const recordingScript = () => runAndReport('script', force.value ? { force: true } : {},
  'Recording script ready: see the links at the top of the Script pane.', 'The recording script could not be made; see Jobs.');
const bumpers = () => runAndReport('bumpers', { lang: props.lang, ...(force.value ? { force: true } : {}) },
  'Intro and outro ready: see the top of the Slides pane.', 'The intro and outro could not be made; see Jobs.');
</script>

<template>
  <q-card flat bordered class="q-mb-md">
    <q-card-section class="q-pb-sm">
      <div class="row items-center q-mb-sm">
        <div class="text-subtitle2">Steps</div>
        <q-space />
        <span class="text-caption text-grey-7">Click a step to run it in that row's language</span>
      </div>
      <div class="stepscroll">
        <div ref="wrap" class="stepwrap">
          <div v-for="r in rows" :key="r.lang" :class="['stepflow', 'steprow', r.lang]">
            <div class="row-label">
              {{ r.lang.toUpperCase() }}
              <q-icon v-if="r.complete" name="task_alt" color="positive" size="14px"><q-tooltip>{{ LANG_NAME[r.lang] }} complete</q-tooltip></q-icon>
            </div>
            <template v-for="({ item, v }, i) in r.items" :key="r.lang + item.name">
              <q-icon v-if="i > 0" name="arrow_forward" size="18px" class="arrow" />
              <div v-if="item.kind === 'wait'" :class="['step', ...v.cls]" :data-step="`${r.lang}-${item.name}`">
                <div class="name"><q-icon :name="v.icon" :color="v.color" size="16px" />{{ item.label }}</div>
                <div class="cap">{{ v.cap }}</div>
                <q-tooltip max-width="320px">{{ v.tip }}</q-tooltip>
              </div>
              <q-btn v-else no-caps flat :class="['step', ...v.cls]" :data-step="`${r.lang}-${item.name}`" :disable="v.disable"
                :aria-label="`Run ${item.name} (${LANG_NAME[r.lang]})`" @click="run(item.name, r.lang)">
                <div class="name"><q-icon :name="v.icon" :color="v.color" size="16px" />{{ item.name }}</div>
                <div class="cap">{{ v.cap }}</div>
                <q-tooltip max-width="340px">{{ v.tip }}</q-tooltip>
              </q-btn>
            </template>
            <template v-if="r.lang === 'en'">
              <q-separator vertical class="q-mx-sm" />
              <q-btn no-caps flat class="step" aria-label="Run qa" @click="run('qa', 'en')">
                <div class="name"><q-icon name="fact_check" size="16px" />qa</div>
                <div class="cap">check everything</div>
                <q-tooltip max-width="340px">{{ STEP_HELP.qa }}</q-tooltip>
              </q-btn>
            </template>
          </div>
          <svg class="steplinks" :width="size.w" :height="size.h" aria-hidden="true">
            <defs>
              <marker id="steplink-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" class="head" />
              </marker>
            </defs>
            <path v-for="l in links" :key="l.key" :d="l.d" class="link" marker-end="url(#steplink-head)" />
          </svg>
        </div>
      </div>
    </q-card-section>
    <q-separator />
    <q-card-section class="row items-center gap-sm q-py-sm">
      <q-checkbox v-model="force" dense label="force re-run">
        <q-tooltip max-width="320px">Rebuild even if the result is already up to date. Normally a step is skipped when nothing it depends on has changed.</q-tooltip>
      </q-checkbox>
      <q-checkbox v-model="noBumpers" dense label="no bumpers">
        <q-tooltip max-width="320px">Leave the intro and outro off the draft video made by compose. Quicker, and the player's times then match the cue sheet exactly. Delivered files are unaffected.</q-tooltip>
      </q-checkbox>
      <q-space />
      <q-btn flat dense no-caps icon="description" label="Recording script" @click="recordingScript">
        <q-tooltip max-width="320px">{{ STEP_HELP.script }} Links appear at the top of the Script pane.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="movie" label="Intro/outro" @click="bumpers">
        <q-tooltip max-width="320px">{{ STEP_HELP.bumpers }} In {{ LANG_NAME[lang] }}, the language chosen at the top right. They appear at the top and bottom of the Slides pane.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="slideshow" label="Teleprompter" :href="`#/prompt/${t.id}`" target="_blank">
        <q-tooltip max-width="320px">Open the narration as a full-screen teleprompter in a new tab. Space plays and pauses; the arrow keys change speed and jump between slides.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="verified" label="Verify files" @click="emit('verify')">
        <q-tooltip max-width="320px">Open each of this topic's files to confirm it is what it claims (not empty, not corrupt), and that delivered files match their checksums. Results show in the Artefacts table.</q-tooltip>
      </q-btn>
    </q-card-section>
  </q-card>
</template>
