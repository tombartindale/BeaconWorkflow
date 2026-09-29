<script setup lang="ts">
// Settings: UI preferences only. Pipeline configuration lives in programme.toml.
import { reactive } from 'vue';
import type { Prefs } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const boot = beacon.boot!;
const p = reactive<Prefs>({ ...boot.prefs, module_columns: { ...{ en: true, zh: true }, ...boot.prefs.module_columns } });
const doctor = boot.doctor;
const tools = doctor?.results || [];

async function save() {
  try {
    boot.prefs = await api<Prefs>('/api/prefs', { method: 'PUT', body: p });
    beacon.toast('Saved');
  } catch (e) { beacon.toast((e as Error).message, true); }
}
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Settings" :sub="`Programme root: ${boot.root}`" />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium">Preferences</q-card-section>
          <q-card-section class="q-gutter-md">
            <q-input v-model="p.operator" outlined dense label="Your name" hint="Recorded against jobs and review decisions" />
            <q-input v-model.number="p.jobs" outlined dense type="number" :min="1" :max="16" label="Topics in parallel" hint="Passed to bcn --jobs" />
            <q-input v-model.number="p.parallel_jobs" outlined dense type="number" :min="1" :max="8" label="Jobs at once" hint="Never two on the same topic" />
            <q-input v-model.number="p.poll_seconds" outlined dense type="number" :min="5" :max="600" label="Status poll (seconds)" hint="File changes also refresh at once" />
            <q-select v-model="p.topic_layout" outlined dense emit-value map-options label="Topic layout"
              :options="[{ label: 'Side by side', value: 'side' }, { label: 'Stacked', value: 'stacked' }]" />
            <div>
              <div class="text-caption text-grey-7">Module grid shows</div>
              <q-checkbox v-model="p.module_columns.en" label="English" />
              <q-checkbox v-model="p.module_columns.zh" label="Mandarin" />
            </div>
          </q-card-section>
          <q-card-actions><q-btn unelevated color="primary" label="Save" @click="save" /></q-card-actions>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium">Tools (bcn doctor)</q-card-section>
          <q-markup-table v-if="doctor" flat dense>
            <thead><tr><th class="text-left">Tool</th><th class="text-left">Pinned</th><th class="text-left">Found</th><th></th></tr></thead>
            <tbody>
              <tr v-for="r in tools" :key="r.topic">
                <td>{{ r.topic }}</td><td class="text-mono">{{ r.pinned }}</td><td class="text-caption">{{ r.found || '—' }}</td>
                <td><StateChip :kind="r.ok ? 'ok' : 'blocked'" :label="r.ok ? 'ok' : 'problem'" /></td>
              </tr>
            </tbody>
          </q-markup-table>
          <q-card-section v-else class="text-grey-7">Checking…</q-card-section>
          <q-card-section class="text-caption text-grey-7">Single user, bound to localhost, no authentication. See the README before exposing it anywhere else.</q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
