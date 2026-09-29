<script setup lang="ts">
// Jobs: running and recent. Two progress bars (run and current topic), ETA from bcn,
// a live log tail, and cancel. No heartbeat for ten seconds reads as stalled.
import { computed, onBeforeUnmount, reactive, watch } from 'vue';
import type { Diagnostic, JobDetail, JobSummary } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, fmtDuration } from '@/format';
import { LIVE_STATES, useBeacon } from '@/stores/beacon';

const props = defineProps<{ openId: number | null }>();
const beacon = useBeacon();
const open = reactive(new Set<number>(props.openId ? [props.openId] : []));
const details = reactive(new Map<number, JobDetail>());  // full records: log and envelopes

function logLine(line: string): { cls: string; text: string } | null {
  let ev: Record<string, unknown>;
  try { ev = JSON.parse(line); } catch { return { cls: '', text: line }; }
  if (ev.event === 'progress') {
    if (ev.heartbeat) return null;
    const pct = ev.topic_pct !== null && ev.topic_pct !== undefined ? `${ev.topic_pct}%` : '';
    return { cls: 'prog', text: `${ev.topic || ''} ${pct} ${ev.message || ''}` };
  }
  if (ev.event === 'done') return { cls: 'prog', text: ev.cancelled ? '— cancelled —' : '— done —' };
  return { cls: ev.level === 'error' ? 'err' : '', text: String(ev.message || line) };
}

async function fetchDetail(id: number) {
  try { details.set(id, await api<JobDetail>(`/api/jobs/${id}`)); } catch { /* gone */ }
}
function toggle(id: number) {
  if (open.has(id)) open.delete(id); else { open.add(id); void fetchDetail(id); }
}
async function cancel(id: number) {
  try { await api(`/api/jobs/${id}/cancel`, { method: 'POST', body: {} }); } catch (e) { beacon.toast((e as Error).message, true); }
}

function view(j: JobSummary & { _seen?: number }) {
  const st = beacon.stateOf(j);
  const p = j.progress || ({} as NonNullable<JobSummary['progress']>);
  const live = LIVE_STATES.includes(st);
  const runPct = j.target_count > 1 ? ((j.target_index + (p.pct || 0) / 100) / j.target_count) * 100 : (p.pct || 0);
  const d = details.get(j.id);
  return {
    j, st, p, live, runPct,
    meta: `#${j.id} · ${j.by || ''} · ${live ? (j.started ? `started ${fmtAgo(j.started, beacon.now)}` : 'waiting') : `finished ${fmtAgo(j.finished, beacon.now)} · ${fmtDuration(j.duration_ms)}`}`,
    progress: [p.topic, p.message, p.items ? `topic ${p.item} of ${p.items}` : null,
      j.target_count > 1 ? `invocation ${j.target_index + 1} of ${j.target_count}` : null,
      p.elapsed_ms ? `elapsed ${fmtDuration(p.elapsed_ms)}` : null, p.eta_ms !== undefined ? `ETA ${fmtDuration(p.eta_ms)}` : null,
      st === 'stalled' ? 'no heartbeat for over 10 s' : null].filter(Boolean).join(' · '),
    diags: (d?.envelopes || []).flatMap((e) => (e.diagnostics || []) as Diagnostic[]).filter((x) => x.level !== 'info').slice(0, 50),
    log: (d?.log || []).slice(-300).map(logLine).filter((x): x is { cls: string; text: string } => x !== null),
  };
}

const jobs = computed(() => [...beacon.jobs.values()].sort((a, b) => b.id - a.id));
const sections = computed(() => {
  const all = jobs.value.map(view);
  return [
    { title: `Running and queued (${all.filter((v) => v.live).length})`, rows: all.filter((v) => v.live), empty: 'Nothing running.' },
    { title: 'Recent', rows: all.filter((v) => !v.live).slice(0, 60), empty: 'No jobs yet.' },
  ];
});

for (const id of open) void fetchDetail(id);
const off = beacon.onJobEvent(({ job, event }) => {
  const d = open.has(job) ? details.get(job) : undefined;
  if (d) d.log = [...d.log, JSON.stringify(event)].slice(-400);
});
onBeforeUnmount(off);
// A job that has just finished: fetch it again for its envelopes.
watch(() => [...open].map((id) => beacon.jobs.get(id)?.state), () => {
  for (const id of open) {
    const j = beacon.jobs.get(id);
    if (j && !LIVE_STATES.includes(j.state) && !details.get(id)?.finished) void fetchDetail(id);
  }
});
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Jobs" sub="Every action is a job. Jobs never run twice on the same topic at once; cancelling lets bcn clean up." />
    <q-card v-for="(sec, k) in sections" :key="k" flat bordered class="q-mb-md">
      <q-card-section class="text-subtitle1 text-weight-medium q-pb-sm">{{ sec.title }}</q-card-section>
      <q-list separator>
        <q-item v-for="v in sec.rows" :key="v.j.id" class="column items-stretch">
          <div class="row items-center q-gutter-sm">
            <StateChip :kind="v.st" />
            <span class="text-weight-medium">{{ v.j.label }}</span>
            <span class="text-caption text-grey-7">{{ v.meta }}</span>
            <span v-if="v.j.exit_code" class="text-caption text-grey-7">exit {{ v.j.exit_code }}</span>
            <q-space />
            <q-btn v-if="v.live" outline dense size="sm" color="negative" no-caps label="Cancel" @click="cancel(v.j.id)" />
            <q-btn flat dense size="sm" no-caps :icon="open.has(v.j.id) ? 'expand_less' : 'expand_more'" :label="open.has(v.j.id) ? 'Hide' : 'Details'" @click="toggle(v.j.id)" />
          </div>
          <div v-if="v.live && v.j.state !== 'queued'" class="q-mt-sm q-gutter-y-xs">
            <div class="row items-center q-gutter-sm no-wrap">
              <span class="text-caption" style="width: 40px">Run</span>
              <q-linear-progress :value="v.runPct / 100" size="10px" rounded class="col" />
              <span class="text-caption" style="width: 40px">{{ v.runPct.toFixed(0) }}%</span>
            </div>
            <div class="row items-center q-gutter-sm no-wrap">
              <span class="text-caption" style="width: 40px">Topic</span>
              <q-linear-progress :value="(v.p.topic_pct || 0) / 100" size="10px" rounded color="secondary" class="col" />
              <span class="text-caption" style="width: 40px">{{ v.p.topic_pct !== null && v.p.topic_pct !== undefined ? `${Math.round(v.p.topic_pct)}%` : '' }}</span>
            </div>
            <div class="text-caption text-grey-7">{{ v.progress }}</div>
          </div>
          <div v-if="open.has(v.j.id)" class="q-mt-sm">
            <q-list v-if="v.diags.length" dense>
              <q-item v-for="(x, i) in v.diags" :key="i" dense>
                <q-item-section side><StateChip :kind="x.level" /></q-item-section>
                <q-item-section>
                  <q-item-label><router-link v-if="x.topic" :to="`/topic/${x.topic}`">{{ x.topic }}</router-link><template v-if="x.topic"> · </template>{{ x.message }}</q-item-label>
                  <q-item-label caption class="text-mono">{{ x.code }}</q-item-label>
                </q-item-section>
              </q-item>
            </q-list>
            <div class="log"><div v-for="(l, i) in v.log" :key="i" :class="l.cls">{{ l.text }}</div></div>
          </div>
        </q-item>
        <q-item v-if="!sec.rows.length"><q-item-section class="text-grey-7">{{ sec.empty }}</q-item-section></q-item>
      </q-list>
    </q-card>
  </q-page>
</template>
