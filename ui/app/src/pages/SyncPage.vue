<script setup lang="ts">
// Sync: pull from and push to the shared OneDrive folder. Both directions are shown as
// previews first (bcn sync --dry-run, which never downloads), and a sync only runs when asked.
import { computed, ref, shallowRef, watch } from 'vue';
import type { SyncEnvelope, SyncPlanItem, SyncResponse } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { confirm } from '@/composables/confirm';
import { fmtAgo, fmtBytes, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

const LIMIT = 200;
const ACTION: Record<string, string> = { copy: 'copy', conflict: 'conflict', one_side: 'one side', check: 'check', failed: 'failed' };
const beacon = useBeacon();
const data = shallowRef<SyncResponse | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);

async function load() {
  try { data.value = await api<SyncResponse>('/api/sync'); error.value = null; } catch (e) { error.value = (e as Error).message; }
}
void load();
watch(() => beacon.status, () => { if (!busy.value) void load(); });

async function run(direction: 'pull' | 'push', extra: Record<string, string | string[]> = {}, label?: string) {
  busy.value = true;
  try {
    const job = await beacon.runJob('sync', ['.'], { [direction]: true, ...extra });
    const env = (await beacon.awaitJob(job.id)).envelopes?.[0];
    if (env) {
      const n = env.diagnostics.filter((d) => d.code === 'SYNC_COPIED').length;
      const bad = env.diagnostics.filter((d) => d.level === 'error');
      beacon.toast(`${label || (direction === 'pull' ? 'Pulled' : 'Pushed')}: ${plural(n, 'file')} copied${bad.length ? `, ${bad.length} need attention` : ''}`, bad.length > 0);
    }
  } catch { /* runJob has said why */ }
  busy.value = false;
  void load();
}

function side(env: SyncEnvelope, direction: 'pull' | 'push') {
  const pull = direction === 'pull';
  const plan = env.plan || [];
  const copies = plan.filter((p) => p.action === 'copy' || p.action === 'check');
  return {
    env, direction, pull, plan, copies,
    title: pull ? 'Pull from OneDrive' : 'Push to OneDrive',
    notConfigured: env.diagnostics.find((d) => d.code === 'SYNC_NOT_CONFIGURED' || d.code === 'SYNC_REMOTE_MISSING'),
    conflicts: plan.filter((p) => p.action === 'conflict'),
    oneSide: plan.filter((p) => p.action === 'one_side'),
    last: pull ? env.last_pull : env.last_push,
    cloud: copies.filter((p) => p.cloud).length,
  };
}
const sides = computed(() => (data.value ? [side(data.value.pull, 'pull'), side(data.value.push, 'push')] : []));
const conflicts = computed(() => (data.value?.pull.plan || []).filter((p) => p.action === 'conflict'));
const copyConflicts = computed(() => (data.value?.pull.diagnostics || []).filter((d) => d.code === 'SYNC_REMOTE_CONFLICT_COPY'));
const remote = computed(() => data.value?.pull.remote || data.value?.push.remote);

async function confirmRun(s: ReturnType<typeof side>) {
  const ok = await confirm({
    title: `${s.title}?`, ok: s.pull ? 'Pull' : 'Push',
    lines: [
      `This copies ${plural(s.copies.length, 'file')} ${s.pull ? 'from OneDrive into the local copy' : 'from the local copy to OneDrive'}.`,
      ...(s.pull && s.cloud ? [`${s.cloud} of them are cloud-only and will be downloaded first.`] : []),
      ...(s.conflicts.length ? [`${plural(s.conflicts.length, 'conflict')} will be left alone.`] : []),
      { text: 'Nothing is ever deleted on either side.', muted: true },
    ],
  });
  if (ok) void run(s.direction);
}
async function keep(p: SyncPlanItem, which: 'remote' | 'local') {
  const remoteWins = which === 'remote';
  const ok = await confirm({
    title: remoteWins ? 'Keep the OneDrive version?' : 'Keep the local version?',
    lines: [remoteWins ? `The local ${p.local} is replaced with ${p.remote}.` : `${p.remote} on OneDrive is replaced with the local ${p.local}.`],
    ok: remoteWins ? 'Keep OneDrive version' : 'Keep local version',
  });
  if (ok) void run(remoteWins ? 'pull' : 'push', { prefer: which, only: [p.local] }, remoteWins ? 'Kept OneDrive version' : 'Kept local version');
}
function checkAgain() { data.value = null; void load(); }
const chip = (a: string) => (a === 'conflict' ? 'blocked' : a === 'copy' ? 'ok' : undefined);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Sync">
      <template #sub>
        <span v-if="remote">OneDrive folder: <span class="text-mono">{{ remote }}</span></span>
        <template v-else>The local working copy and the shared OneDrive folder are synced by hand, in each direction.</template>
      </template>
      <q-btn outline no-caps icon="refresh" label="Check again" :disable="busy" @click="checkAgain" />
    </PageHeader>
    <q-banner v-if="error" class="bg-negative text-white" rounded>{{ error }}</q-banner>
    <div v-else-if="!data" class="text-grey-7"><q-spinner /> Comparing with OneDrive…</div>
    <template v-else>
      <q-banner v-if="copyConflicts.length" class="bg-negative text-white q-mb-md" rounded>
        <strong>OneDrive conflict copies: </strong>{{ copyConflicts.map((d) => d.file).join(', ') }}. Someone needs to decide which version is right in OneDrive and delete the other; they are not synced.
      </q-banner>
      <q-card v-if="conflicts.length" flat bordered class="q-mb-md">
        <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Changed on both sides ({{ conflicts.length }})</q-card-section>
        <q-card-section class="text-caption text-grey-7">Neither version has been overwritten. Choose which one to keep; the other side is replaced with it.</q-card-section>
        <q-list separator>
          <q-item v-for="p in conflicts" :key="p.local">
            <q-item-section><q-item-label class="text-mono">{{ p.local }}</q-item-label><q-item-label caption class="text-mono">{{ p.remote }}</q-item-label></q-item-section>
            <q-item-section side class="row items-center gap-sm" style="flex-direction: row">
              <q-btn outline dense size="sm" no-caps label="Keep OneDrive version" :disable="busy" @click="keep(p, 'remote')" />
              <q-btn outline dense size="sm" no-caps label="Keep local version" :disable="busy" @click="keep(p, 'local')" />
            </q-item-section>
          </q-item>
        </q-list>
      </q-card>
      <div class="row q-col-gutter-md">
        <div v-for="s in sides" :key="s.direction" class="col-12 col-lg-6">
          <q-card flat bordered>
            <q-card-section class="row items-center">
              <div class="text-subtitle1 text-weight-medium">{{ s.title }}</div>
              <q-space />
              <span v-if="!s.notConfigured" class="text-caption text-grey-7">{{ s.last ? `last ${s.pull ? 'pulled' : 'pushed'} ${fmtAgo(s.last, beacon.now)}` : `never ${s.pull ? 'pulled' : 'pushed'}` }}</span>
            </q-card-section>
            <q-card-section v-if="s.notConfigured">
              <p>{{ s.notConfigured.message }}</p>
              <p v-if="s.notConfigured.hint" class="text-grey-7">{{ s.notConfigured.hint }}</p>
            </q-card-section>
            <template v-else>
              <q-card-section class="row items-center gap-sm q-pt-none">
                <span><strong>{{ s.copies.length }}</strong> to copy · {{ s.env.counts?.in_sync || 0 }} in sync</span>
                <StateChip v-if="s.conflicts.length" kind="blocked" :label="plural(s.conflicts.length, 'conflict')" />
                <StateChip v-if="s.oneSide.length" :label="`${s.oneSide.length} on one side only`" />
                <q-space />
                <q-btn unelevated color="primary" no-caps :disable="busy || !s.copies.length" :loading="busy"
                  :label="s.copies.length ? `${s.pull ? 'Pull' : 'Push'} ${plural(s.copies.length, 'file')}` : 'Nothing to copy'" @click="confirmRun(s)" />
              </q-card-section>
              <q-markup-table v-if="s.plan.length" flat dense wrap-cells>
                <thead><tr><th></th><th class="text-left">Local</th><th class="text-left">OneDrive</th><th class="text-left">Why</th></tr></thead>
                <tbody>
                  <tr v-for="(p, i) in s.plan.slice(0, LIMIT)" :key="i">
                    <td><StateChip :kind="chip(p.action)" :label="ACTION[p.action] || p.action" /></td>
                    <td class="text-mono text-caption">{{ p.local }}</td>
                    <td class="text-mono text-caption">{{ p.remote }} <StateChip v-if="p.cloud" kind="cloud" label="☁" /></td>
                    <td class="text-caption text-grey-7">{{ p.reason }}{{ p.bytes ? ` · ${fmtBytes(p.bytes)}` : '' }}</td>
                  </tr>
                </tbody>
              </q-markup-table>
              <q-card-section v-if="s.plan.length > LIMIT" class="text-caption text-grey-7">…and {{ s.plan.length - LIMIT }} more.</q-card-section>
              <q-card-section v-if="!s.plan.length" class="text-grey-7">Everything is in sync.</q-card-section>
            </template>
          </q-card>
        </div>
      </div>
      <p class="text-caption text-grey-7 q-mt-md">Pull brings scripts, course maps, activities, asset requests and the editor's video and subtitles into the local copy.
        Push sends local script changes, review decisions and finished delivery packages back.
        Pulled files are marked as new, so anything built from an older version shows as stale.</p>
      <q-expansion-item v-if="data.pull.ignored?.length" dense class="q-mt-md" :label="`${data.pull.ignored.length} files in OneDrive are not part of the pipeline and are left alone`">
        <ul class="text-mono text-caption"><li v-for="f in data.pull.ignored.slice(0, 300)" :key="f">{{ f }}</li></ul>
      </q-expansion-item>
    </template>
  </q-page>
</template>
