<script setup lang="ts">
// Module: units down, topics across, one split cell per topic (English | Mandarin).
// Stage is shown by position; colour marks only stale and blocked. Bulk actions live here,
// where the scope is visible, and say what they will do before they run.
import { computed, reactive } from 'vue';
import type { LangState, TopicStatus } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StagePips from '@/components/StagePips.vue';
import StateChip from '@/components/StateChip.vue';
import { fileUrl } from '@/api';
import { confirm } from '@/composables/confirm';
import { LANG_NAME, plural, STAGE_LABEL, STEP_HELP, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const BULK = ['validate', 'render', 'script', 'bumpers', 'cues', 'compose', 'package'];
const props = defineProps<{ module: string }>();
const beacon = useBeacon();

const state = reactive({
  selected: new Set<string>(),
  lang: 'en' as 'en' | 'zh',    // language for bulk actions
  force: false,
  filter: { stage: '', lang: 'en' as 'en' | 'zh' | 'both', stale: false, blocked: false },
});

const env = computed(() => beacon.status);
const rows = computed(() => (env.value?.results || []).filter((r) => r.module === props.module));
const m = computed(() => env.value?.summary.modules[props.module]);
const units = computed(() => [...new Set(rows.value.map((r) => r.unit))].sort());
const codes = computed(() => [...new Set(rows.value.map((r) => r.code))].sort());
const grid = computed(() => units.value.map((unit) => ({
  unit, cells: codes.value.map((c) => rows.value.find((x) => x.unit === unit && x.code === c) ?? null),
  activity: (m.value?.documents || []).find((d) => d.path === `${props.module}/${unit}/activity.md`) ?? null,
})));
const stageOptions = computed(() => [{ label: 'any stage', value: '' },
  ...[...new Set([...(rows.value[0]?.en.stages || []), ...(rows.value[0]?.zh.stages || [])])].map((s) => ({ label: STAGE_LABEL[s], value: s }))]);
const cols = computed(() => beacon.boot?.prefs.module_columns || { en: true, zh: true });
const langs = computed(() => (['en', 'zh'] as const).filter((l) => cols.value[l] !== false));

function matches(r: TopicStatus) {
  const f = state.filter;
  const sides = f.lang === 'both' ? [r.en, r.zh] : [r[f.lang]];
  if (f.stage && !sides.some((s) => s.stage === f.stage)) return false;
  if (f.stale && !sides.some((s) => s.stale)) return false;
  if (f.blocked && !sides.some((s) => s.blocked)) return false;
  return true;
}
const visible = computed(() => rows.value.filter(matches));
const nSelected = computed(() => [...state.selected].filter((id) => rows.value.some((r) => r.topic === id)).length);

function toggle(id: string, on: boolean) {
  if (on) state.selected.add(id); else state.selected.delete(id);
}
function cellClick(e: MouseEvent, r: TopicStatus) {
  if (e.metaKey || e.shiftKey || e.ctrlKey) { e.preventDefault(); toggle(r.topic, !state.selected.has(r.topic)); }
}
const isCloud = (r: TopicStatus) => r.hydration === 'cloud' || r.hydration === 'partial';
const halfTip = (s: LangState, lang: string) => [`${lang.toUpperCase()}: ${STAGE_LABEL[s.stage]}`,
  s.stale ? `stale (${s.stale_steps.join(', ')})` : '', s.blocked ? `blocked: ${s.blockers.map((b) => b.code).join(', ')}` : '',
  s.next ? `next: ${s.next}` : ''].filter(Boolean).join('\n');

// Collapse a selection to the widest directories it fully covers, so bcn can run them with --jobs.
function targetsFor(ids: Set<string>) {
  const onDisk = rows.value.filter((r) => r.has_dir);
  const byUnit = new Map<string, string[]>();
  for (const r of onDisk) { if (!byUnit.has(r.unit)) byUnit.set(r.unit, []); byUnit.get(r.unit)!.push(r.topic); }
  const all = [...byUnit.entries()];
  if (all.every(([, ts]) => ts.every((t) => ids.has(t))) && ids.size === onDisk.length) return [props.module];
  const out: string[] = [];
  for (const [u, ts] of all) {
    const picked = ts.filter((t) => ids.has(t));
    if (!picked.length) continue;
    if (picked.length === ts.length) out.push(`${props.module}/${u}`);
    else out.push(...picked.map(topicPath));
  }
  return out;
}

async function bulk(command: string) {
  const ids = new Set([...state.selected].filter((id) => rows.value.find((r) => r.topic === id && r.has_dir)));
  if (!ids.size) return;
  const targets = targetsFor(ids);
  const lang = ['cues', 'script'].includes(command) ? 'en' : state.lang;  // English only
  const force = state.force;
  const cli = `bcn ${command}${lang === 'zh' ? ' --lang zh' : ''}${force ? ' --force' : ''}`;
  const ok = await confirm({
    title: `Run ${command} on ${plural(ids.size, 'topic')}?`,
    lines: [
      { text: `This runs ${cli} on ${plural(ids.size, 'topic')} (${command === 'cues' ? 'English; Mandarin inherits cues' : LANG_NAME[state.lang]}), as ${plural(targets.length, 'invocation')}:`, strong: cli },
      { text: targets.join('  '), mono: true },
      force ? 'Force rebuilds topics whose outputs are already current.' : 'Topics whose outputs are already current are skipped.',
    ],
  });
  if (ok) await beacon.runJob(command, targets, force ? { lang, force: true } : { lang });
}

const quizzes = computed(() => (m.value?.documents || []).filter((d) => d.quiz));
async function exportQuizzes() {
  const n = quizzes.value.length;
  const ok = await confirm({
    title: `Export ${plural(n, 'quiz', 'quizzes')} for the LMS?`,
    lines: [
      { text: `This runs bcn qti ${props.module}, writing one QTI 2.1 package per unit quiz to ${props.module}/build/qti/.`, strong: `bcn qti ${props.module}` },
      'Quizzes whose package is already current are skipped. A quiz with errors gets no package.',
    ],
  });
  if (ok) await beacon.runJob('qti', [props.module], {});
}

async function exportCourseMapPdf() {
  const job = await beacon.runJob('coursemap', [props.module], {});
  const result = await beacon.awaitJob(job.id);
  if (result.state === 'done') beacon.toast('Course map PDF ready: see Module documents.');
  else beacon.toast('The course map PDF could not be made; see Jobs.', true);
}

// Unit activities show in their unit's grid row (see grid.activity above), not here; this box is module-wide documents only.
const docs = computed(() => (m.value?.documents || [])
  .filter((d) => !d.path.endsWith('/activity.md') && (d.exists || d.path.endsWith('course-map.md'))));
</script>

<template>
  <q-page padding class="page-max">
    <div v-if="!env" class="text-grey-7 q-pa-lg">Reading the programme…</div>
    <div v-else-if="!m" class="text-grey-7 q-pa-lg">No module {{ module }}.</div>
    <template v-else>
      <PageHeader :title="`${module}${m.title ? ' · ' + m.title : ''}`" :crumbs="[{ label: 'Programme', to: '/' }, { label: module }]"
        :sub="`${m.topics} topics · English ${m.complete.en} complete · Mandarin ${m.complete.zh} complete`">
        <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
        <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
        <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
        <q-btn v-if="quizzes.length" outline no-caps icon="quiz" label="Export quizzes to LMS" @click="exportQuizzes">
          <q-tooltip max-width="320px">{{ STEP_HELP.qti }}</q-tooltip>
        </q-btn>
        <q-btn outline no-caps label="Run QA on module" @click="beacon.runJob('qa', [module], {})" />
      </PageHeader>

      <q-card v-if="m.documents?.length" flat bordered class="q-mb-md">
        <q-card-section class="row items-center gap-sm q-pb-none">
          <div class="text-subtitle1 text-weight-medium">Module documents</div>
          <StateChip v-if="m.errors" kind="error" :label="plural(m.errors, 'error')" />
          <StateChip v-else kind="ok" label="no problems" />
        </q-card-section>
        <q-card-section class="row items-center gap-sm">
          <template v-for="d in docs" :key="d.path">
            <q-btn v-if="d.exists" outline dense no-caps :to="`/doc/${d.path}`" :label="d.path.slice(module.length + 1)">
              <q-badge v-if="d.errors" color="negative" floating>{{ d.errors }}</q-badge>
              <q-badge v-else-if="d.warnings" color="warning" floating>{{ d.warnings }}</q-badge>
            </q-btn>
            <q-btn v-else flat dense no-caps disable :label="`${d.path.slice(module.length + 1)} — missing`">
              <q-tooltip>Not in the working copy yet</q-tooltip>
            </q-btn>
            <q-btn v-if="d.quiz?.exists" flat dense no-caps icon="download" :color="d.quiz.stale ? 'warning' : 'primary'"
              :label="d.quiz.stale ? 'QTI (out of date)' : 'QTI'" :href="fileUrl(d.quiz.package, null, 'download=1')">
              <q-tooltip>{{ d.quiz.stale ? 'The quiz has changed since this package was made; export again.' : `${plural(d.quiz.questions, 'question')}, ready to import into the LMS` }}</q-tooltip>
            </q-btn>
            <q-btn v-if="d.pdf?.exists" flat dense no-caps icon="download" :color="d.pdf.stale ? 'warning' : 'primary'"
              :label="d.pdf.stale ? 'PDF (out of date)' : 'PDF'" :href="fileUrl(d.pdf.path, null, 'download=1')">
              <q-tooltip max-width="320px">{{ d.pdf.stale ? 'course-map.md has changed since this PDF was made; export again.' : STEP_HELP.coursemap }}</q-tooltip>
            </q-btn>
            <q-btn v-if="d.pdf && (!d.pdf.exists || d.pdf.stale)" outline dense no-caps icon="picture_as_pdf"
              :label="d.pdf.exists ? 'Update PDF' : 'Export PDF'" @click="exportCourseMapPdf">
              <q-tooltip max-width="320px">{{ STEP_HELP.coursemap }}</q-tooltip>
            </q-btn>
          </template>
        </q-card-section>
      </q-card>

      <q-card flat bordered>
        <q-card-section class="row items-center gap-sm">
          <strong class="text-caption">Show</strong>
          <q-select v-model="state.filter.stage" dense outlined emit-value map-options :options="stageOptions" style="min-width: 150px" aria-label="Stage" />
          <q-select v-model="state.filter.lang" dense outlined emit-value map-options style="min-width: 150px" aria-label="Language"
            :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }, { label: 'either language', value: 'both' }]" />
          <q-checkbox v-model="state.filter.stale" dense label="stale" />
          <q-checkbox v-model="state.filter.blocked" dense label="blocked" />
          <span class="text-caption text-grey-7">{{ visible.length }} of {{ rows.length }} match</span>
          <q-space />
          <q-btn flat dense no-caps label="Select matching" @click="visible.filter((r) => r.has_dir).forEach((r) => state.selected.add(r.topic))" />
          <q-btn flat dense no-caps label="Clear selection" @click="state.selected.clear()" />
        </q-card-section>
        <q-separator />
        <q-card-section class="row items-center gap-sm bg-grey-2 bulkbar">
          <strong>{{ nSelected ? `${nSelected} selected` : 'Select topics to act on them' }}</strong>
          <q-btn-toggle v-model="state.lang" dense no-caps unelevated toggle-color="primary" :disable="!nSelected"
            :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]" />
          <q-btn v-for="c in BULK" :key="c" outline dense no-caps :label="c" :disable="!nSelected" @click="bulk(c)">
            <q-tooltip max-width="320px">{{ STEP_HELP[c] }} Runs on the selected topics; you confirm first.</q-tooltip>
          </q-btn>
          <q-checkbox v-model="state.force" dense label="force" :disable="!nSelected" />
          <q-space />
          <span class="text-caption text-grey-7">Click a cell to open it; ⌘-click or tick to select.</span>
        </q-card-section>
        <q-card-section class="scroll">
          <table class="topic-grid">
            <thead><tr><th></th><th v-for="c in codes" :key="c">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="row in grid" :key="row.unit">
                <th class="unit">
                  {{ row.unit }}<span class="t">{{ m.unit_titles?.[row.unit] || '' }}</span>
                  <span v-if="row.activity?.exists" class="row items-center gap-xs q-mt-xs">
                    <q-btn outline dense no-caps size="sm" :to="`/doc/${row.activity.path}`" label="activity">
                      <q-badge v-if="row.activity.errors" color="negative" floating>{{ row.activity.errors }}</q-badge>
                      <q-badge v-else-if="row.activity.warnings" color="warning" floating>{{ row.activity.warnings }}</q-badge>
                    </q-btn>
                    <q-btn v-if="row.activity.quiz?.exists" flat dense no-caps size="sm" icon="download"
                      :color="row.activity.quiz.stale ? 'warning' : 'primary'"
                      :label="row.activity.quiz.stale ? 'QTI (out of date)' : 'QTI'"
                      :href="fileUrl(row.activity.quiz.package, null, 'download=1')">
                      <q-tooltip>{{ row.activity.quiz.stale ? 'The quiz has changed since this package was made; export again.' : `${plural(row.activity.quiz.questions, 'question')}, ready to import into the LMS` }}</q-tooltip>
                    </q-btn>
                  </span>
                </th>
                <td v-for="(r, i) in row.cells" :key="codes[i]">
                  <a v-if="r" :href="`#/topic/${r.topic}`" @click="cellClick($event, r)"
                    :class="['cell', { one: langs.length === 1, cloud: isCloud(r), planned: r.en.stage === 'planned', selected: state.selected.has(r.topic), dim: !matches(r) }]">
                    <span class="code">{{ r.code }}{{ isCloud(r) ? ' ☁' : '' }}</span>
                    <input v-if="r.en.stage !== 'planned'" type="checkbox" class="sel" :aria-label="`Select ${r.topic}`"
                      :checked="state.selected.has(r.topic)" @click.stop="toggle(r.topic, ($event.target as HTMLInputElement).checked)">
                    <div v-for="lang in langs" :key="lang" :class="['half', { stale: r[lang].stale, blocked: r[lang].blocked }]">
                      <StagePips :index="r[lang].stage_index" :total="r[lang].stages.length" />
                      <span class="lbl">{{ STAGE_LABEL[r[lang].stage] }}</span>
                      <q-tooltip style="white-space: pre-line">{{ r.topic }}{{ r.title ? ` — ${r.title}` : '' }}{{ isCloud(r) ? '\ncloud-only files' : '' }}{{ '\n' + halfTip(r[lang], lang) }}</q-tooltip>
                    </div>
                    <span v-if="r.unreviewed_mistranscriptions" class="unrev">✎{{ r.unreviewed_mistranscriptions }}<q-tooltip>suspected mis-transcriptions to review</q-tooltip></span>
                  </a>
                </td>
              </tr>
            </tbody>
          </table>
        </q-card-section>
      </q-card>
      <p class="text-caption text-grey-7 q-mt-md">
        Each cell: English on the left, Mandarin on the right. Filled pips show how far the topic has got.
        <StateChip kind="stale" label="amber" /> is stale (built from older inputs);
        <StateChip kind="blocked" label="red" /> is blocked (needs a person); a dashed cell has cloud-only files.
      </p>
    </template>
  </q-page>
</template>

<style scoped>
body.body--dark .bulkbar { background: rgba(255, 255, 255, .04) !important; }
</style>
