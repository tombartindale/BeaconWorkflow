<script setup lang="ts">
// Translation round trip: a batch handover out, and a batch check on the way back,
// so everything wrong goes back to the translator in one message.
import { computed, ref, shallowRef } from 'vue';
import type { BcnTranslationEnvelope, JobSummary, TranslationItem, TranslationListResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, fmtBytes, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

type Env = BcnTranslationEnvelope & { return_to_translator?: Array<{ topic: string; message: string }> };
const beacon = useBeacon();
const scope = ref('');
const lists = shallowRef<TranslationListResponse>({ exports: [], returned: [] });
const lastImport = shallowRef<{ item: TranslationItem; env: Env | undefined } | null>(null);
const busy = ref(false);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

async function refresh() {
  try { lists.value = await api<TranslationListResponse>('/api/translation'); } catch (e) { beacon.toast((e as Error).message, true); }
}
void refresh();

async function doExport() {
  if (!scope.value) return;
  busy.value = true;
  try {
    const job = await beacon.runJob('translation', [scope.value], { export: true });
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env && !env.ok) beacon.toast(env.diagnostics.filter((d) => d.level === 'error').map((d) => d.message).join(' ') || 'Export failed.', true);
  } finally {
    busy.value = false;
    void refresh();
  }
}

async function upload(file: File | undefined) {
  if (!file) return;
  try {
    await api(`/api/translation/upload?name=${encodeURIComponent(file.name)}`, { raw: await file.arrayBuffer() });
    beacon.toast(`Uploaded ${file.name}`);
  } catch (e) { beacon.toast((e as Error).message, true); }
  void refresh();
}
function onDrop(e: DragEvent) { over.value = false; void upload(e.dataTransfer?.files[0]); }

async function doImport(item: TranslationItem) {
  busy.value = true;
  try {
    const job = await api<JobSummary>('/api/translation/import', { body: { source: item.path } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    lastImport.value = { item, env: done.envelopes?.[0] as unknown as Env | undefined };
  } catch (e) { beacon.toast((e as Error).message, true); }
  busy.value = false;
}

const scopes = computed(() => Object.entries(beacon.status?.summary.modules || {}).sort()
  .flatMap(([m, info]) => [m, ...info.units.map((u) => `${m}/${u}`)]));
const ready = computed(() => (beacon.status?.results || [])
  .filter((r) => r.zh.next === 'translation_export' && (!scope.value || r.path.startsWith(scope.value))));
const zips = computed(() => lists.value.exports.filter((x) => x.kind === 'zip'));

const message = computed(() => {
  const items = lastImport.value?.env?.return_to_translator || [];
  if (!items.length) return '';
  const byTopic = new Map<string, string[]>();
  for (const x of items) { if (!byTopic.has(x.topic)) byTopic.set(x.topic, []); byTopic.get(x.topic)!.push(x.message); }
  return ['The following returned files need correcting. Slide breaks must match the English exactly, and SRT timings must be identical cue for cue.', '',
    ...[...byTopic.entries()].flatMap(([t, ms]) => [`${t}:`, ...ms.map((m) => `  - ${m}`), ''])].join('\n');
});
const copy = () => navigator.clipboard.writeText(message.value).then(() => beacon.toast('Copied'));
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Translation" sub="Export sends the reviewed English SRT and narration-stripped slides. Import places the returned files and checks parity and timings across the batch." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Export</q-card-section>
          <q-card-section class="row items-center gap-sm">
            <q-select v-model="scope" dense outlined :options="scopes" label="Module or unit" style="min-width: 220px" />
            <q-btn unelevated color="primary" no-caps label="Export batch" :disable="!scope || busy" :loading="busy" @click="doExport" />
          </q-card-section>
          <q-card-section class="text-caption text-grey-7 q-pt-none">
            {{ plural(ready.length, 'topic') }} in {{ scope || 'the programme' }} ready to send.
            A batch goes all or nothing: every topic in scope must have passed validate and subtitles, with its mis-transcriptions reviewed.
          </q-card-section>
        </q-card>
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Exports</q-card-section>
          <q-list v-if="zips.length" separator>
            <q-item v-for="x in zips" :key="x.path">
              <q-item-section><q-item-label class="text-mono">{{ x.name }}</q-item-label>
                <q-item-label caption>{{ fmtAgo(x.mtime, beacon.now) }} · {{ fmtBytes(x.bytes) }}</q-item-label></q-item-section>
              <q-item-section side><q-btn outline dense size="sm" no-caps icon="download" label="Download" :href="fileUrl(x.path, null, 'download=1')" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-grey-7">No exports yet.</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Import</q-card-section>
          <q-card-section>
            <div :class="['dropzone q-pa-lg text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': over }]"
              style="border: 2px dashed var(--line)" @click="fileInput?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
              <q-icon name="upload_file" size="md" class="q-mb-sm" /><br>
              Drop the returned .zip here, or click to choose. Or put a folder in translation/returned/.
              <input ref="fileInput" type="file" accept=".zip" class="hidden" @change="upload(($event.target as HTMLInputElement).files?.[0])">
            </div>
          </q-card-section>
          <q-list v-if="lists.returned.length" separator>
            <q-item v-for="x in lists.returned" :key="x.path">
              <q-item-section><q-item-label class="text-mono">{{ x.name }}</q-item-label>
                <q-item-label caption>{{ x.kind }} · {{ fmtAgo(x.mtime, beacon.now) }}</q-item-label></q-item-section>
              <q-item-section side><q-btn unelevated dense size="sm" color="primary" no-caps label="Import and check" :disable="busy" @click="doImport(x)" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-grey-7">Nothing returned yet.</q-card-section>
          <q-card-section class="text-caption text-grey-7">Expected names: &lt;topic_id&gt;.zh.md and &lt;topic_id&gt;.zh.srt.</q-card-section>
        </q-card>
        <q-card v-if="lastImport?.env" flat bordered>
          <q-card-section class="row items-center gap-sm">
            <div class="text-subtitle1 text-weight-medium">Imported {{ lastImport.item.name }}</div>
            <StateChip :kind="lastImport.env.ok ? 'ok' : 'blocked'" :label="lastImport.env.ok ? 'all passed' : 'problems found'" />
          </q-card-section>
          <q-markup-table flat dense>
            <thead><tr><th class="text-left">Topic</th><th class="text-left">Files</th><th class="text-left">Parity</th><th class="text-left">SRT timings</th></tr></thead>
            <tbody>
              <tr v-for="x in lastImport.env.results" :key="x.topic">
                <td><router-link :to="`/topic/${x.topic}`">{{ x.topic }}</router-link></td>
                <td class="text-caption">{{ Object.entries(x.files || {}).map(([k, v]) => `${k}: ${v}`).join(', ') }}</td>
                <td v-for="(v, k) in [x.validate_ok, x.subtitles_ok]" :key="k">
                  <StateChip v-if="v !== undefined && v !== null" :kind="v ? 'ok' : 'blocked'" :label="v ? 'ok' : 'fail'" /><template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </q-markup-table>
          <q-card-section v-if="message" class="q-gutter-y-sm">
            <div class="text-subtitle2">Back to the translator, in one message</div>
            <q-input :model-value="message" type="textarea" outlined readonly input-style="height: 200px" />
            <q-btn outline no-caps icon="content_copy" label="Copy message" @click="copy" />
          </q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
