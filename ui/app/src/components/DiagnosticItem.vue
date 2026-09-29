<script setup lang="ts">
// One finding from bcn: level, message, where, and the hint. Pages add their own links
// and actions in the default slot.
import { computed } from 'vue';
import type { Diagnostic } from '@beacon/shared';
import StateChip from './StateChip.vue';

const props = defineProps<{ d: Diagnostic; showCode?: boolean; showLang?: boolean }>();
const loc = computed(() => [props.showCode === false ? null : props.d.code, props.showLang ? props.d.lang?.toUpperCase() : null,
  props.d.file ? `${props.d.file}${props.d.line ? `:${props.d.line}` : ''}` : null,
  props.d.slide ? `slide ${props.d.slide}` : null, props.d.data?.step ? `from ${props.d.data.step}` : null]
  .filter(Boolean).join(' · '));
</script>

<template>
  <q-item>
    <q-item-section side top><StateChip :kind="d.level" /></q-item-section>
    <q-item-section>
      <q-item-label><slot name="message">{{ d.message }}</slot></q-item-label>
      <q-item-label v-if="loc" caption class="text-mono">{{ loc }}</q-item-label>
      <q-item-label v-if="d.hint" caption class="text-italic">{{ d.hint }}</q-item-label>
      <div v-if="$slots.default" class="row items-center q-gutter-sm q-mt-xs"><slot /></div>
    </q-item-section>
  </q-item>
</template>
