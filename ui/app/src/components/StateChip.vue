<script setup lang="ts">
// A small labelled state. Colour follows the house rule: amber for stale and warnings,
// red for blocked and errors, green for done; everything else is neutral.
import { computed } from 'vue';

const props = defineProps<{ kind?: string; label?: string | number; icon?: string }>();
const STYLE: Record<string, { color?: string; textColor?: string; outline?: boolean; cls?: string }> = {
  stale: { color: 'warning', textColor: 'white' },
  warn: { color: 'warning', outline: true },
  blocked: { color: 'negative', textColor: 'white' },
  error: { color: 'negative', outline: true },
  failed: { color: 'negative', textColor: 'white' },
  interrupted: { color: 'negative', outline: true },
  ok: { color: 'positive', outline: true },
  done: { color: 'positive', outline: true },
  running: { color: 'primary', textColor: 'white' },
  stalled: { color: 'warning', textColor: 'white' },
  queued: { outline: true },
  cancelled: { outline: true },
  cloud: { outline: true, cls: 'chip-cloud' },
  info: { outline: true },
};
const s = computed(() => STYLE[props.kind || ''] || { outline: true });
</script>

<template>
  <q-chip dense square size="12px" :icon="icon" :color="s.color" :text-color="s.textColor" :outline="s.outline"
    :class="['q-ma-none', s.cls]"><slot>{{ label ?? kind }}</slot></q-chip>
</template>

<style scoped>
.chip-cloud { border-style: dashed; }
</style>
