<script setup lang="ts">
// Programme: the landing screen. Four numbers, then one row per module. Rows are links and nothing else.
import { computed, onMounted, ref, watch } from 'vue';
import type { SyncResponse } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, plural, STAGE_LABEL } from '@/format';
import { useBeacon } from '@/stores/beacon';

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

// The stage ladder: one column per pipeline step, English on top, Mandarin below. Mandarin
// can only be sent for translation once the English subtitles are done, so its row starts
// after English "Cued", under English "Packaged", which runs alongside the translation.
// Each cell shows how many of the module's topics have reached at least that stage.
const BRANCH_AFTER = 'cued';
const ladder = computed(() => {
  const en = stages.value.en.filter((st) => st !== 'planned');
  const zh = stages.value.zh.filter((st) => st !== 'not_sent');
  const branch = en.indexOf(BRANCH_AFTER) + 1;           // column (0-based) where Mandarin starts
  const columns = Math.max(en.length, branch + zh.length);
  return { en, zh, branch, columns };
});
// Topics at this stage or any later one. Stage lists are in pipeline order.
const reached = (counts: Record<string, number>, lang: 'en' | 'zh', st: string) => {
  const all = stages.value[lang];
  return all.slice(all.indexOf(st)).reduce((n, s2) => n + (counts[s2] || 0), 0);
};
const cells = (m: { topics: number; en: Record<string, number>; zh: Record<string, number> }, lang: 'en' | 'zh') =>
  ladder.value[lang].map((st, i) => {
    const n = reached(m[lang], lang, st);
    return { st, n, pct: m.topics ? (100 * n) / m.topics : 0,
      col: (lang === 'en' ? i : ladder.value.branch + i) + 2 };  // +2: grid column 1 is the language label
  });
const gridStyle = computed(() => ({ gridTemplateColumns: `28px repeat(${ladder.value.columns}, minmax(56px, 1fr)) 96px` }));
</script>

<template>
  <q-page padding class="page-max">
    <div v-if="!env" class="text-grey-7 q-pa-lg">Reading the programme…</div>
    <template v-else>
      <PageHeader title="Programme" :sub="`${s.topics} topics across ${modules.length} modules`" />

      <q-card v-if="syncLine" flat bordered class="q-mb-md cursor-pointer" @click="$router.push('/sync')">
        <q-card-section class="row items-center gap-sm q-py-sm">
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
          <!-- Column headings: laid out exactly like a module row, so the columns line up. -->
          <q-item dense class="q-pt-sm">
            <q-item-section class="prog-name"></q-item-section>
            <q-item-section>
              <div class="ladder" :style="gridStyle">
                <span v-for="(st, i) in ladder.en" :key="'en-' + st" class="ladder-h" :style="{ gridRow: 1, gridColumn: i + 2 }">{{ STAGE_LABEL[st] }}</span>
                <span v-for="(st, i) in ladder.zh" :key="'zh-' + st" class="ladder-h zh" :style="{ gridRow: 2, gridColumn: ladder.branch + i + 2 }">{{ STAGE_LABEL[st] }}</span>
              </div>
            </q-item-section>
            <q-item-section side class="prog-flags"></q-item-section>
          </q-item>
          <q-item v-for="[name, m] in modules" :key="name" clickable :to="`/module/${name}`" class="q-py-md">
            <q-item-section class="prog-name">
              <q-item-label class="text-weight-bold">{{ name }}</q-item-label>
              <q-item-label caption>{{ m.title }}</q-item-label>
              <q-item-label caption>{{ m.topics }} topics · {{ m.units.length }} units</q-item-label>
            </q-item-section>
            <q-item-section>
              <div class="ladder" :style="gridStyle">
                <template v-for="lang in LANGS" :key="lang">
                  <span class="ladder-lang" :style="{ gridRow: lang === 'en' ? 1 : 2 }">{{ lang.toUpperCase() }}</span>
                  <span v-if="lang === 'zh'" class="ladder-branch" :style="{ gridRow: 2, gridColumn: ladder.branch + 1 }">
                    ↳ after subtitles
                  </span>
                  <div v-for="c in cells(m, lang)" :key="c.st" :class="['ladder-cell', lang, { done: c.n === m.topics && c.n > 0 }]"
                    :style="{ gridRow: lang === 'en' ? 1 : 2, gridColumn: c.col }">
                    <span class="fill" :style="{ width: `${c.pct}%` }"></span>
                    <span class="n">{{ c.n }}</span>
                    <q-tooltip>{{ lang === 'en' ? 'English' : 'Mandarin' }} · {{ STAGE_LABEL[c.st] }}: {{ c.n }} of {{ m.topics }} topics have got this far</q-tooltip>
                  </div>
                  <span class="ladder-done text-caption text-grey-7" :style="{ gridRow: lang === 'en' ? 1 : 2, gridColumn: ladder.columns + 2 }">
                    {{ m.complete[lang] }}/{{ m.topics }} complete
                  </span>
                </template>
              </div>
            </q-item-section>
            <q-item-section side class="prog-flags row items-center gap-xs" style="flex-direction: row; flex-wrap: wrap; justify-content: flex-end; align-content: center">
              <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
              <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
              <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
              <StateChip v-if="m.unreviewed" kind="warn" :label="`${m.unreviewed} to proofread`" />
              <StateChip v-if="m.errors" kind="error" :label="`module documents: ${plural(m.errors, 'error')}`" />
            </q-item-section>
          </q-item>
          <q-item v-if="!modules.length"><q-item-section class="text-grey-7">No modules found.</q-item-section></q-item>
        </q-list>
        <q-card-section class="text-caption text-grey-7">
          Each cell counts the topics that have reached at least that stage. Mandarin starts once the English
          subtitles are done, when a topic can be sent for translation.
        </q-card-section>
      </q-card>
    </template>
  </q-page>
</template>
