<script setup lang="ts">
import { computed, inject } from 'vue';
import type { LangState } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StagePips from '@/components/StagePips.vue';
import StateChip from '@/components/StateChip.vue';
import { NEXT_LABEL, STAGE_LABEL } from '@/format';
import { TOPIC } from './context';

const layout = defineModel<'side' | 'stacked'>('layout', { required: true });
const t = inject(TOPIC)!;
const st = computed(() => t.status.value);
const title = computed(() => st.value?.title || (t.show.value?.en?.front?.title as string | undefined) || '');
const langs = computed(() => (st.value ? ([['en', st.value.en], ['zh', st.value.zh]] as Array<[string, LangState]>) : []));
const blockers = computed(() => (st.value ? [...st.value.en.blockers.map((b) => ({ ...b, lang: 'EN' })), ...st.value.zh.blockers.map((b) => ({ ...b, lang: 'ZH' }))] : []));
</script>

<template>
  <PageHeader :title="`${t.id} · ${title}`"
    :crumbs="[{ label: 'Programme', to: '/' }, { label: t.id.slice(0, 6), to: `/module/${t.id.slice(0, 6)}` }, { label: t.id.slice(7, 10) }]">
    <template #sub>
      <div v-if="st" class="row items-center gap-md">
        <span v-for="[lang, s] in langs" :key="lang" class="row items-center gap-xs">
          <strong class="text-caption">{{ lang.toUpperCase() }}</strong>
          <StagePips :index="s.stage_index" :total="s.stages.length" size="lg" />
          <span>{{ STAGE_LABEL[s.stage] }}</span>
          <StateChip v-if="s.stale" kind="stale" :label="`stale: ${s.stale_steps.join(', ')}`" />
          <StateChip v-if="s.blocked" kind="blocked" label="blocked" />
          <StateChip v-if="s.complete" kind="ok" label="complete" />
          <span v-if="s.next" class="text-caption">next: {{ NEXT_LABEL[s.next] || s.next }}</span>
        </span>
        <StateChip v-if="st.hydration !== 'local' && st.hydration !== 'unknown'" kind="cloud" :label="`☁ ${st.hydration}`" />
        <span v-if="st.minutes" class="text-caption">{{ st.minutes }} min · {{ st.outcomes.join(', ') }}</span>
      </div>
      <q-list v-if="blockers.length" dense class="q-mt-sm">
        <q-item v-for="(b, i) in blockers" :key="i" dense class="q-px-none">
          <q-item-section side><StateChip kind="blocked" :label="`${b.lang} ${b.code}`" /></q-item-section>
          <q-item-section>
            {{ b.message }}
            <span v-if="b.code === 'FS_NOT_HYDRATED'" class="text-caption"> In Finder, choose Always Keep on This Device for this folder.</span>
          </q-item-section>
        </q-item>
      </q-list>
    </template>
    <span class="text-caption text-grey-7">Layout</span>
    <q-btn-toggle v-model="layout" dense no-caps unelevated toggle-color="primary"
      :options="[{ label: 'Side by side', value: 'side' }, { label: 'Stacked', value: 'stacked' }]" />
  </PageHeader>
</template>
