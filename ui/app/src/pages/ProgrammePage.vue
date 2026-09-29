<script setup lang="ts">
// Programme: the landing screen. Four numbers, then one row per module. Rows are links and nothing else.
import { computed, onMounted, ref, watch } from 'vue';
import type { SyncResponse } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, plural, STAGE_LABEL } from '@/format';
import { useBeacon } from '@/stores/beacon';

// A neutral ramp: later stages darker. Colour is kept for stale and blocked elsewhere.
const RAMP = {
  en: ['#d9d5cc', '#c3cbd2', '#a6b4c0', '#8799a9', '#6a8093', '#4d677d', '#2f4b62'],
  zh: ['#d9d5cc', '#c3cbd2', '#a6b4c0', '#7c91a3', '#557087', '#2f4b62'],
};
const LANGS = ['en', 'zh'] as const;
const beacon = useBeacon();

const sync = ref<SyncResponse | null>(null);  // bcn sync dry runs, for the OneDrive line
const loadSync = () => api<SyncResponse>('/api/sync').then((d) => { sync.value = d; }).catch(() => {});
onMounted(loadSync);
watch(() => beacon.status, loadSync);

const env = computed(() => beacon.status);
const s = computed(() => env.value!.summary);
const stages = computed(() => ({ en: env.value?.results[0]?.en.stages || [], zh: env.value?.results[0]?.zh.stages || [] }));
const modules = computed(() => Object.entries(env.value?.summary.modules || {}).sort(([a], [b]) => a.localeCompare(b)));

const kpis = computed(() => {
  const jobs = [...beacon.jobs.values()];
  const running = jobs.filter((j) => ['running', 'stalled'].includes(j.state)).length;
  const queued = jobs.filter((j) => j.state === 'queued').length;
  const d = s.value.diagnostics;
  return [
    { v: `${s.value.complete.en} / ${s.value.complete.zh}`, l: 'Topics complete', s: 'English / Mandarin' },
    { v: s.value.blocked, l: 'Topics blocked', s: 'need a person', alert: s.value.blocked > 0 },
    { v: running, l: 'Jobs running', s: queued ? `${queued} queued` : 'none queued' },
    { v: d.error + d.warn, l: 'Diagnostics outstanding', alert: d.error > 0,
      s: `${d.error} errors · ${d.warn} warnings${s.value.unreviewed ? ` · ${s.value.unreviewed} to proofread` : ''}` },
  ];
});

const syncLine = computed(() => {
  if (!sync.value) return null;
  const { pull, push } = sync.value;
  if ((pull.diagnostics || []).some((d) => d.code === 'SYNC_NOT_CONFIGURED')) return null;
  const n = (e: typeof pull) => (e.plan || []).filter((p) => p.action === 'copy' || p.action === 'check').length;
  return { last: pull.last_pull ?? null, pull: n(pull), push: n(push),
    conflicts: (pull.plan || []).filter((p) => p.action === 'conflict').length };
});

const segments = (counts: Record<string, number>, lang: 'en' | 'zh', total: number) => stages.value[lang]
  .map((st, i) => ({ st, n: counts[st] || 0, colour: RAMP[lang][i] }))
  .filter((x) => x.n)
  .map((x) => ({ ...x, width: `${(100 * x.n) / total}%` }));
const ariaBar = (counts: Record<string, number>, lang: 'en' | 'zh') =>
  stages.value[lang].map((st) => `${STAGE_LABEL[st]} ${counts[st] || 0}`).join(', ');
</script>

<template>
  <q-page padding class="page-max">
    <div v-if="!env" class="text-grey-7 q-pa-lg">Reading the programme…</div>
    <template v-else>
      <PageHeader title="Programme" :sub="`${s.topics} topics across ${modules.length} modules`" />

      <q-card v-if="syncLine" flat bordered class="q-mb-md cursor-pointer" @click="$router.push('/sync')">
        <q-card-section class="row items-center q-gutter-sm q-py-sm">
          <q-icon name="cloud" size="sm" color="primary" />
          <strong>OneDrive</strong>
          <span class="text-grey-7">{{ syncLine.last ? `last pulled ${fmtAgo(syncLine.last, beacon.now)}` : 'never pulled' }}</span>
          <StateChip v-if="syncLine.pull" kind="warn" :label="`${syncLine.pull} to pull`" />
          <StateChip v-else kind="ok" label="up to date" />
          <StateChip v-if="syncLine.push" :label="`${syncLine.push} to push`" />
          <StateChip v-if="syncLine.conflicts" kind="blocked" :label="plural(syncLine.conflicts, 'conflict')" />
          <q-space />
          <span class="text-caption">Sync →</span>
        </q-card-section>
      </q-card>

      <div class="row q-col-gutter-md q-mb-md">
        <div v-for="k in kpis" :key="k.l" class="col-12 col-sm-6 col-md-3">
          <q-card flat bordered>
            <q-card-section>
              <div :class="['text-h4 text-weight-bold', { 'text-negative': k.alert }]">{{ k.v }}</div>
              <div>{{ k.l }}</div>
              <div class="text-caption text-grey-7">{{ k.s }}</div>
            </q-card-section>
          </q-card>
        </div>
      </div>

      <q-card flat bordered>
        <q-list separator>
          <q-item v-for="[name, m] in modules" :key="name" clickable :to="`/module/${name}`" class="q-py-md">
            <q-item-section style="max-width: 260px">
              <q-item-label class="text-weight-bold">{{ name }}</q-item-label>
              <q-item-label caption>{{ m.title }}</q-item-label>
              <q-item-label caption>{{ m.topics }} topics · {{ m.units.length }} units</q-item-label>
            </q-item-section>
            <q-item-section>
              <div v-for="lang in LANGS" :key="lang" class="row items-center no-wrap q-gutter-sm q-my-xs">
                <span class="text-caption text-weight-bold" style="width: 22px">{{ lang.toUpperCase() }}</span>
                <div class="stackbar" role="img" :aria-label="ariaBar(m[lang], lang)">
                  <span v-for="x in segments(m[lang], lang, m.topics)" :key="x.st" :style="{ width: x.width, background: x.colour }">
                    <q-tooltip>{{ STAGE_LABEL[x.st] }}: {{ x.n }}</q-tooltip>
                  </span>
                </div>
                <span class="text-caption text-grey-7" style="width: 90px">{{ m.complete[lang] }}/{{ m.topics }} complete</span>
              </div>
            </q-item-section>
            <q-item-section side class="row q-gutter-xs" style="flex-direction: row; max-width: 360px; flex-wrap: wrap; justify-content: flex-end">
              <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
              <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
              <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
              <StateChip v-if="m.unreviewed" kind="warn" :label="`${m.unreviewed} to proofread`" />
              <StateChip v-if="m.errors" kind="error" :label="`module documents: ${plural(m.errors, 'error')}`" />
            </q-item-section>
          </q-item>
          <q-item v-if="!modules.length"><q-item-section class="text-grey-7">No modules found.</q-item-section></q-item>
        </q-list>
        <q-card-section class="q-gutter-y-xs">
          <div v-for="lang in LANGS" :key="lang" class="row q-gutter-md text-caption text-grey-7 legend">
            <strong>{{ lang.toUpperCase() }}</strong>
            <span v-for="(st, i) in stages[lang]" :key="st"><i :style="{ background: RAMP[lang][i] }"></i>{{ STAGE_LABEL[st] }}</span>
          </div>
        </q-card-section>
      </q-card>
    </template>
  </q-page>
</template>
