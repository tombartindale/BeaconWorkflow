<script setup lang="ts">
// Diagnostics: everything outstanding, grouped by code, because failures cluster and
// fixing a class of them at once is how the work goes. Mis-transcriptions get their own
// tab: a proofreading task, with both readings side by side.
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { Diagnostic, DiagnosticsResponse } from '@beacon/shared';
import { api } from '@/api';
import AckControls from '@/components/AckControls.vue';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtTime, LEVEL_ORDER, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const props = defineProps<{ tab: string }>();
const beacon = useBeacon();
const router = useRouter();
const f = reactive({ level: 'warn', module: '', lang: '', code: '' });
const env = shallowRef<DiagnosticsResponse | null>(null);
const error = ref<string | null>(null);
const corrections = reactive<Record<string, string>>({});  // mis-transcription id -> corrected text being typed

async function load() {
  try { env.value = await api<DiagnosticsResponse>('/api/diagnostics?path=.'); error.value = null; } catch (e) { error.value = (e as Error).message; }
}
void load();
let timer: ReturnType<typeof setTimeout> | undefined;
watch(() => beacon.status, () => { clearTimeout(timer); timer = setTimeout(load, 300); });
onBeforeUnmount(() => clearTimeout(timer));

const outstanding = computed(() => env.value?.outstanding || []);
const moduleOptions = computed(() => [{ label: 'all modules', value: '' }, ...Object.keys(beacon.status?.summary.modules || {}).sort().map((m) => ({ label: m, value: m }))]);
const codeOptions = computed(() => [{ label: 'all codes', value: '' }, ...[...new Set(outstanding.value.map((d) => d.code))].sort().map((c) => ({ label: c, value: c }))]);
const mtOpen = computed(() => outstanding.value.filter((d) => d.code === 'CUE_MISTRANSCRIPTION' && !d.data?.review).length);
const tab = computed({
  get: () => props.tab,
  set: (v: string) => { void router.push(v === 'codes' ? '/diagnostics' : '/diagnostics/mistranscriptions'); },
});

const groups = computed(() => {
  const list = outstanding.value.filter((d) =>
    LEVEL_ORDER[d.level] <= LEVEL_ORDER[f.level] &&
    (!f.module || (d.topic || d.file || '').startsWith(f.module)) &&
    (!f.lang || d.lang === f.lang) &&
    (!f.code || d.code === f.code) &&
    (d.code !== 'CUE_MISTRANSCRIPTION' || d.level !== 'info'));
  const byCode = new Map<string, Diagnostic[]>();
  for (const d of list) {
    if (!byCode.has(d.code)) byCode.set(d.code, []);
    byCode.get(d.code)!.push(d);
  }
  return [...byCode.entries()]
    .sort((a, b) => (LEVEL_ORDER[a[1][0].level] - LEVEL_ORDER[b[1][0].level]) || (b[1].length - a[1].length))
    .map(([code, items]) => {
      const topics = [...new Set(items.map((d) => d.topic).filter((t): t is string => Boolean(t)))].sort();
      const steps = [...new Set(items.map((d) => d.data?.step).filter(Boolean))] as string[];
      const langs = [...new Set(items.map((d) => d.lang).filter(Boolean))] as string[];
      const only = steps.length === 1 ? steps[0] : null;
      return { code, items, topics, langs, step: only && topics.length && only !== 'qa' ? only : null };
    });
});

function rerun(g: (typeof groups.value)[number]) {
  const lang = g.step === 'cues' ? 'en' : (g.langs.length === 1 ? g.langs[0] : 'en');
  void beacon.runJob(g.step!, g.topics.map(topicPath), { lang });
}

const mt = computed(() => {
  const items = outstanding.value.filter((d) => d.code === 'CUE_MISTRANSCRIPTION' && (!f.module || (d.topic || '').startsWith(f.module)));
  return [
    { title: 'To review', rows: items.filter((d) => !d.data?.review), empty: 'All reviewed.' },
    { title: 'Reviewed', rows: items.filter((d) => d.data?.review), empty: '' },
  ];
});
const review = (d: Diagnostic, args: Record<string, string | boolean>) =>
  beacon.runJob('review', [topicPath(d.topic!)], { item: d.data!.id!, ...args });
function correct(d: Diagnostic) {
  const text = (corrections[d.data!.id!] ?? d.data!.script ?? '').trim();
  if (text) void review(d, { correct: text });
}
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Diagnostics"
      :sub="env ? `${env.counts.error} errors · ${env.counts.warn} warnings · ${env.counts.info} info, from current step results` : 'Loading…'" />
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>
    <q-tabs v-model="tab" dense no-caps align="left" class="q-mb-md" active-color="primary" indicator-color="primary">
      <q-tab name="codes" label="By code" />
      <q-tab name="mistranscriptions" :label="`Mis-transcriptions${mtOpen ? ` (${mtOpen})` : ''}`" />
    </q-tabs>
    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row items-center q-gutter-sm">
        <q-select v-if="tab === 'codes'" v-model="f.level" dense outlined emit-value map-options style="min-width: 190px" aria-label="Level"
          :options="[{ label: 'errors', value: 'error' }, { label: 'errors and warnings', value: 'warn' }, { label: 'everything', value: 'info' }]" />
        <q-select v-model="f.module" dense outlined emit-value map-options :options="moduleOptions" style="min-width: 150px" aria-label="Module" />
        <q-select v-if="tab === 'codes'" v-model="f.lang" dense outlined emit-value map-options style="min-width: 150px" aria-label="Language"
          :options="[{ label: 'both languages', value: '' }, { label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]" />
        <q-select v-if="tab === 'codes'" v-model="f.code" dense outlined emit-value map-options :options="codeOptions" style="min-width: 220px" aria-label="Code" />
        <q-space />
        <q-btn flat dense no-caps icon="refresh" label="Refresh" @click="load" />
      </q-card-section>
    </q-card>

    <div v-if="!env" class="text-grey-7">Loading…</div>

    <template v-else-if="tab === 'codes'">
      <q-card v-if="!groups.length" flat bordered><q-card-section class="text-grey-7">Nothing outstanding at this level.</q-card-section></q-card>
      <q-card v-for="g in groups" :key="g.code" flat bordered class="q-mb-sm">
        <q-expansion-item>
          <template #header>
            <q-item-section avatar><q-badge color="grey-7" :label="g.items.length" /></q-item-section>
            <q-item-section>
              <q-item-label class="row items-center q-gutter-sm"><StateChip :kind="g.items[0].level" /><code>{{ g.code }}</code></q-item-label>
              <q-item-label caption>{{ beacon.codes.get(g.code)?.description || '' }}</q-item-label>
            </q-item-section>
            <q-item-section v-if="g.step" side>
              <q-btn outline dense size="sm" no-caps :label="`Re-run ${g.step} on ${g.topics.length}`" @click.stop="rerun(g)" />
            </q-item-section>
          </template>
          <q-card-section class="q-gutter-xs q-pt-none">
            <q-chip v-for="t in g.topics" :key="t" dense clickable outline :label="t" @click="$router.push(`/topic/${t}`)" />
          </q-card-section>
          <q-list separator>
            <DiagnosticItem v-for="(d, i) in g.items.slice(0, 200)" :key="i" :d="d" :show-code="false" show-lang>
              <template #message>
                <router-link v-if="d.topic" :to="`/topic/${d.topic}`">{{ d.topic }}</router-link>
                <router-link v-else-if="d.data?.document && d.file" :to="`/doc/${d.file}${d.line ? `?line=${d.line}` : ''}`">{{ d.file }}</router-link>
                {{ d.topic || (d.data?.document && d.file) ? ' · ' : '' }}{{ d.message }}
              </template>
              <AckControls :d="d" />
            </DiagnosticItem>
          </q-list>
        </q-expansion-item>
      </q-card>
    </template>

    <template v-else>
      <p class="text-grey-7">The partner translates from this SRT, so a mishearing here reaches Mandarin.
        Accept leaves the SRT as it is; Correct changes the delivered subtitle text (timings never change). Run subtitles and package afterwards.</p>
      <template v-for="section in mt" :key="section.title">
        <q-card v-if="section.empty || section.rows.length" flat bordered class="q-mb-md">
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-sm">{{ section.title }} ({{ section.rows.length }})</q-card-section>
          <q-list separator>
            <q-item v-for="d in section.rows" :key="d.data!.id" :class="{ 'text-grey-6': d.data?.review }">
              <q-item-section style="max-width: 190px">
                <router-link :to="`/topic/${d.topic}`">{{ d.topic }}</router-link>
                <q-item-label caption>slide {{ d.data!.slide }} · cue {{ d.data!.cue ?? '—' }}</q-item-label>
                <router-link v-if="d.data!.time !== null && d.data!.time !== undefined" class="text-caption" :to="`/topic/${d.topic}?t=${d.data!.time}`">▶ play {{ fmtTime(d.data!.time) }}</router-link>
              </q-item-section>
              <q-item-section>
                <q-item-label caption>Script (approved)</q-item-label>
                <q-item-label>{{ d.data!.script }}</q-item-label>
              </q-item-section>
              <q-item-section>
                <q-item-label caption>SRT (captioned)</q-item-label>
                <q-item-label class="text-warning">{{ d.data!.srt }}</q-item-label>
              </q-item-section>
              <q-item-section side style="min-width: 340px">
                <div v-if="d.data?.review" class="row items-center q-gutter-sm">
                  <StateChip kind="ok" :label="d.data.review.decision === 'accept' ? 'accepted' : `corrected to “${d.data.review.text}”`" />
                  <span class="text-caption">{{ d.data.review.by || '' }}</span>
                  <q-btn flat dense size="sm" no-caps label="Undo" @click="review(d, { clear: true })" />
                </div>
                <div v-else class="row items-center q-gutter-sm no-wrap">
                  <q-btn outline dense size="sm" no-caps label="Accept" @click="review(d, { accept: true })">
                    <q-tooltip>The SRT reading is fine as it is</q-tooltip>
                  </q-btn>
                  <q-input :model-value="corrections[d.data!.id!] ?? d.data!.script" dense outlined class="col" aria-label="Corrected subtitle text"
                    @update:model-value="(v) => (corrections[d.data!.id!] = String(v ?? ''))" />
                  <q-btn unelevated dense size="sm" color="primary" no-caps label="Correct" @click="correct(d)">
                    <q-tooltip>The delivered subtitle should read this</q-tooltip>
                  </q-btn>
                </div>
              </q-item-section>
            </q-item>
            <q-item v-if="!section.rows.length"><q-item-section class="text-grey-7">{{ section.empty }}</q-item-section></q-item>
          </q-list>
        </q-card>
      </template>
      <q-card v-if="!mt[0].rows.length && !mt[1].rows.length" flat bordered><q-card-section class="text-grey-7">No suspected mis-transcriptions.</q-card-section></q-card>
    </template>
  </q-page>
</template>
