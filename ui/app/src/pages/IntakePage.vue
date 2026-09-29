<script setup lang="ts">
// Intake: paste what Claude produced, and bcn identifies, checks, places and validates it.
// The only place the UI writes content, and it writes nothing itself: bcn intake does.
import { computed, ref, shallowRef, watch } from 'vue';
import type { BcnIntakeEnvelope, Diagnostic, JobSummary } from '@beacon/shared';
import { api } from '@/api';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { useBeacon } from '@/stores/beacon';

const LABEL: Record<string, string> = { written: 'written', unchanged: 'already identical', exists_differs: 'exists and differs: not written',
  refused: 'refused: not in course map', would_write: 'would be written' };
const DRAFT = 'intake-draft';
const beacon = useBeacon();

const read = () => { try { return sessionStorage.getItem(DRAFT) || ''; } catch { return ''; } };
const text = ref(read());
const path = ref('.');
const busy = ref(false);
const result = shallowRef<{ env: BcnIntakeEnvelope | undefined; dryRun: boolean } | null>(null);
const error = ref<string | null>(null);
watch(text, (t) => { try { sessionStorage.setItem(DRAFT, t); } catch { /* private window: fine */ } });

const moduleOptions = computed(() => [{ label: 'any module', value: '.' }, ...Object.keys(beacon.status?.summary.modules || {}).sort().map((m) => ({ label: m, value: m }))]);
const env = computed(() => result.value?.env);

async function submit(dryRun: boolean) {
  busy.value = true; error.value = null; result.value = null;
  try {
    const job = await api<JobSummary>('/api/intake', { body: { text: text.value, dry_run: dryRun, path: path.value } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    const e = done.envelopes?.[0] as unknown as BcnIntakeEnvelope | undefined;
    result.value = { env: e, dryRun };
    if (!e) error.value = 'bcn intake produced no result; see the job log.';
    else if (!dryRun && e.results.every((r) => ['written', 'unchanged'].includes(r.action as string))) {
      try { sessionStorage.removeItem(DRAFT); } catch { /* fine */ }
    }
  } catch (e) { error.value = (e as Error).message; }
  busy.value = false;
}

const errsFor = (topic: string) => ((env.value?.diagnostics || []) as Diagnostic[]).filter((d) => d.topic === topic && !d.code.startsWith('INTAKE_'));
const diffClass = (l: string) => (l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '');
const good = (action: unknown) => ['written', 'unchanged', 'would_write'].includes(action as string);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Intake" sub="Paste, and bcn identifies each topic by its front matter, checks it against the course map, places it, and validates it. It never overwrites a file that differs; it shows the diff instead." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <q-card flat bordered>
          <q-card-section>
            <q-input v-model="text" type="textarea" outlined input-style="height: 58vh; font-family: var(--mono); font-size: 12.5px"
              placeholder="Paste one topic or a whole unit, front matter included. Code fences are fine." />
          </q-card-section>
          <q-card-actions class="q-px-md q-pb-md">
            <span class="text-caption text-grey-7 q-mr-sm">Restrict to</span>
            <q-select v-model="path" dense outlined emit-value map-options :options="moduleOptions" style="min-width: 150px" />
            <q-space />
            <q-btn outline no-caps label="Check only" :disable="busy || !text.trim()" @click="submit(true)" />
            <q-btn unelevated color="primary" no-caps :loading="busy" label="Place and validate" :disable="busy || !text.trim()" @click="submit(false)" />
          </q-card-actions>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>
        <q-card v-if="env" flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium">
            {{ result!.dryRun ? 'Check' : 'Result' }}: {{ env.topics_found ?? env.results.length }} topic{{ env.results.length === 1 ? '' : 's' }} found
          </q-card-section>
          <q-list separator>
            <q-item v-for="r in env.results" :key="r.topic" class="column items-stretch">
              <div class="row items-center gap-sm">
                <StateChip :kind="good(r.action) ? 'ok' : 'blocked'" :label="LABEL[r.action as string] || (r.action as string) || 'failed'" />
                <router-link v-if="r.path" :to="`/topic/${r.topic}`">{{ r.topic }}</router-link><strong v-else>{{ r.topic }}</strong>
                <StateChip v-if="r.validate_ok !== undefined && r.validate_ok !== null" :kind="r.validate_ok ? 'ok' : 'error'" :label="r.validate_ok ? 'validates' : 'validation errors'" />
              </div>
              <q-list v-if="errsFor(r.topic).length" dense>
                <DiagnosticItem v-for="(d, i) in errsFor(r.topic)" :key="i" :d="d" />
              </q-list>
              <div v-if="r.diff" class="diff q-mt-sm"><div v-for="(l, i) in (r.diff as string).split('\n')" :key="i" :class="diffClass(l)">{{ l || ' ' }}</div></div>
            </q-item>
          </q-list>
        </q-card>
        <q-card v-else flat bordered><q-card-section class="text-grey-7">Results appear here.</q-card-section></q-card>
      </div>
    </div>
  </q-page>
</template>
