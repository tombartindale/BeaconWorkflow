<script setup lang="ts">
// Run any step on this topic, in the language chosen on the left.
import { computed, inject, ref } from 'vue';
import { LANG_NAME, STEP_HELP } from '@/format';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

const STEPS = ['validate', 'render', 'cues', 'subtitles', 'compose', 'package', 'qa'];
const emit = defineEmits<{ verify: [] }>();
const t = inject(TOPIC)!;
const beacon = useBeacon();
const lang = ref<'en' | 'zh'>('en');
const force = ref(false);
const noBumpers = ref(true);
const next = computed(() => (t.status.value ? t.status.value[lang.value].next : null));
const englishOnly = (s: string) => (s === 'cues' || s === 'qa') && lang.value === 'zh';

async function run(step: string) {
  const args: Record<string, string | boolean> = { lang: step === 'cues' ? 'en' : lang.value };
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
const bumpers = () => runAndReport('bumpers', { lang: lang.value, ...(force.value ? { force: true } : {}) },
  'Intro and outro ready: see the top of the Slides pane.', 'The intro and outro could not be made; see Jobs.');
</script>

<template>
  <q-card flat bordered class="q-mb-md">
    <q-card-section class="row items-center q-gutter-sm">
      <q-btn-toggle v-model="lang" dense no-caps unelevated toggle-color="primary"
        :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]">
        <q-tooltip>Which language the buttons to the right work on.</q-tooltip>
      </q-btn-toggle>
      <q-separator vertical />
      <span v-for="s in STEPS" :key="s">
        <q-btn :unelevated="next === s" :outline="next !== s" :color="next === s ? 'primary' : undefined" dense no-caps
          :label="s" :disable="englishOnly(s)" @click="run(s)" />
        <q-tooltip max-width="340px">{{ STEP_HELP[s] }}{{ next === s ? ' This is the next step for this topic.' : '' }}{{ englishOnly(s) ? ' (English only: switch to English to run it.)' : '' }}</q-tooltip>
      </span>
      <q-separator vertical />
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
        <q-tooltip max-width="320px">{{ STEP_HELP.bumpers }} In {{ LANG_NAME[lang] }}, the language chosen on the left. They appear at the top and bottom of the Slides pane.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="slideshow" label="Teleprompter" :href="`#/prompt/${t.id}`" target="_blank">
        <q-tooltip max-width="320px">Open the narration as a full-screen teleprompter in a new tab. Space plays and pauses; the arrow keys change speed and jump between slides.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="fact_check" label="Verify files" @click="emit('verify')">
        <q-tooltip max-width="320px">Open each of this topic's files to confirm it is what it claims (not empty, not corrupt), and that delivered files match their checksums. Results show in the Artefacts table.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps icon="play_circle" label="Jobs" to="/jobs">
        <q-tooltip>Running and recent jobs, with progress, logs and cancel.</q-tooltip>
      </q-btn>
    </q-card-section>
  </q-card>
</template>
