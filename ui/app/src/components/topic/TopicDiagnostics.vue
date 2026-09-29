<script setup lang="ts">
import { computed, inject } from 'vue';
import type { Diagnostic } from '@beacon/shared';
import AckControls from '@/components/AckControls.vue';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import { fmtTime, LEVEL_ORDER } from '@/format';
import { TOPIC } from './context';

const props = defineProps<{ diags: Diagnostic[] }>();
const t = inject(TOPIC)!;
const sorted = computed(() => [...props.diags].sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]));
const scriptLang = (d: Diagnostic) => (d.file === 'topic.zh.md' ? 'zh' : 'en');
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center q-pb-sm">
      <div class="text-subtitle1 text-weight-medium">Diagnostics</div>
      <q-space />
      <router-link to="/diagnostics/mistranscriptions" class="text-caption">Proofread mis-transcriptions →</router-link>
    </q-card-section>
    <q-list v-if="sorted.length" separator>
      <DiagnosticItem v-for="(d, i) in sorted" :key="i" :d="d" show-lang>
        <q-btn v-if="d.file === 'topic.md' || d.file === 'topic.zh.md'" flat dense size="sm" no-caps icon="edit"
          :label="d.line ? `Edit line ${d.line}` : 'Edit script'" :to="`/edit/${t.id}?lang=${scriptLang(d)}${d.line ? `&line=${d.line}` : ''}`" />
        <AckControls :d="d" />
        <q-btn v-if="d.data?.time !== undefined && d.data?.time !== null" flat dense size="sm" no-caps icon="play_arrow"
          :label="`Play at ${fmtTime(d.data.time)}`" :to="`/topic/${t.id}?t=${d.data.time}`" />
      </DiagnosticItem>
    </q-list>
    <q-card-section v-else class="text-grey-7">Nothing outstanding.</q-card-section>
  </q-card>
</template>
