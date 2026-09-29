<script setup lang="ts">
// Document viewer: a module document (course map, activity, assignment…) with line
// numbers and its problems marked on the lines they refer to. Read-only.
import { computed, nextTick, onMounted, ref } from 'vue';
import type { Diagnostic, DiagnosticsResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import { useBeacon } from '@/stores/beacon';
import { plural, STEP_HELP } from '@/format';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';

const props = defineProps<{ path: string; line: number | null }>();
const module = props.path.split('/')[0];
const text = ref<string | null>(null);
const diags = ref<Diagnostic[]>([]);
const error = ref<string | null>(null);

const byLine = computed(() => {
  const m = new Map<number | null, Diagnostic[]>();
  for (const d of diags.value) {
    const k = d.line ?? null;
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(d);
  }
  return m;
});
const lines = computed(() => (text.value ?? '').replace(/\r\n/g, '\n').split('\n'));
const worst = (ds: Diagnostic[]) => (ds.some((d) => d.level === 'error') ? 'error' : 'warn');
// A unit quiz: export it for the LMS and download the package, from what bcn status reports.
const beacon = useBeacon();
const quiz = computed(() => beacon.status?.summary.modules[module]?.documents.find((d) => d.path === props.path)?.quiz ?? null);
const unitTarget = props.path.split('/').slice(0, 2).join('/');
async function exportQuiz() {
  await beacon.runJob('qti', [unitTarget], { force: true });  // a button press always rebuilds; it takes milliseconds
}
const scrollTo = (n: number) => document.getElementById(`L${n}`)?.scrollIntoView({ block: 'center' });

onMounted(async () => {
  try {
    const res = await fetch(`/files/${props.path.split('/').map(encodeURIComponent).join('/')}?view=1`);
    if (!res.ok) throw new Error(res.status === 404 ? `${props.path} does not exist.` : `${res.status} ${res.statusText}`);
    const body = await res.text();
    const d = await api<DiagnosticsResponse>(`/api/diagnostics?path=${encodeURIComponent(module)}`);
    diags.value = (d.outstanding || []).filter((x) => x.file === props.path);
    text.value = body;
  } catch (e) {
    error.value = (e as Error).message;
    return;
  }
  if (props.line) { await nextTick(); scrollTo(props.line); }
});
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader :title="path" :crumbs="[{ label: 'Programme', to: '/' }, { label: module, to: `/module/${module}` }, { label: path.slice(module.length + 1) }]"
      :sub="error ? '' : diags.length ? `${diags.length} problem${diags.length === 1 ? '' : 's'}` : 'No problems found.'">
      <template v-if="quiz">
        <q-btn outline no-caps icon="quiz" :label="quiz.exists ? 'Export again' : 'Export to LMS'" :disable="diags.some((d) => d.level === 'error')" @click="exportQuiz">
          <q-tooltip max-width="320px">{{ diags.some((d) => d.level === 'error') ? 'Fix the errors below first: a quiz with errors is not exported.' : STEP_HELP.qti }}</q-tooltip>
        </q-btn>
        <q-btn v-if="quiz.exists" unelevated no-caps icon="download" :color="quiz.stale ? 'warning' : 'primary'"
          :label="quiz.stale ? 'Download QTI (out of date)' : `Download QTI · ${plural(quiz.questions, 'question')}`"
          :href="fileUrl(quiz.package, null, 'download=1')">
          <q-tooltip v-if="quiz.stale">The quiz has changed since this package was made. Export again first.</q-tooltip>
        </q-btn>
      </template>
      <q-btn outline no-caps icon="open_in_new" label="Open raw" :href="fileUrl(path, null, 'view=1')" target="_blank" />
    </PageHeader>
    <q-banner v-if="error" class="bg-negative text-white" rounded>{{ error }}</q-banner>
    <template v-else-if="text !== null">
      <q-card v-if="diags.length" flat bordered class="q-mb-md">
        <q-list separator>
          <DiagnosticItem v-for="(d, i) in diags" :key="i" :d="d">
            <q-btn v-if="d.line" flat dense size="sm" no-caps :label="`Go to line ${d.line}`" @click="scrollTo(d.line!)" />
          </DiagnosticItem>
        </q-list>
      </q-card>
      <q-card flat bordered>
        <table class="doclines">
          <tbody>
            <template v-for="(l, i) in lines" :key="i">
              <tr :id="`L${i + 1}`" :class="byLine.get(i + 1) ? `flag-${worst(byLine.get(i + 1)!)}` : ''">
                <td class="ln">{{ i + 1 }}</td><td>{{ l || ' ' }}</td>
              </tr>
              <tr v-if="byLine.get(i + 1)" class="note">
                <td></td>
                <td><div v-for="(d, j) in byLine.get(i + 1)" :key="j" class="row items-center gap-xs no-wrap"><StateChip :kind="d.level" /><span>{{ d.message }}</span></div></td>
              </tr>
            </template>
          </tbody>
        </table>
      </q-card>
    </template>
  </q-page>
</template>
