<script setup lang="ts">
// Acknowledge a validation finding as deliberate (bcn ack), or withdraw it.
// Renders nothing for findings that cannot be acknowledged.
import { computed } from 'vue';
import type { Diagnostic } from '@beacon/shared';
import { confirm } from '@/composables/confirm';
import { fmtAgo, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';
import StateChip from './StateChip.vue';

const props = defineProps<{ d: Diagnostic }>();
const beacon = useBeacon();
const fp = computed(() => props.d.data?.fingerprint);
const ack = computed(() => props.d.data?.acknowledged);
const path = computed(() => (props.d.topic ? topicPath(props.d.topic) : ''));

async function acknowledge() {
  const note = await confirm({
    title: 'Acknowledge this finding?',
    lines: [props.d.message, 'It will be recorded in the topic\'s review.json with your name, and stop blocking. If the text changes, it will be flagged again.'],
    prompt: { placeholder: 'Why is this fine? e.g. a conceptual example, not a schedule' },
    ok: 'Acknowledge',
  });
  if (note === false) return;
  await beacon.runJob('ack', [path.value], { fingerprint: fp.value!, ...(typeof note === 'string' && note ? { note } : {}) });
}
</script>

<template>
  <template v-if="fp && d.topic">
    <span v-if="ack" class="row items-center q-gutter-xs">
      <StateChip kind="ok" label="acknowledged" />
      <span class="text-caption text-grey-7">by {{ ack.by || '?' }} {{ fmtAgo(ack.at) }}{{ ack.note ? `: “${ack.note}”` : '' }}</span>
      <q-btn flat dense size="sm" no-caps label="Undo" @click="beacon.runJob('ack', [path], { fingerprint: fp!, clear: true })" />
    </span>
    <q-btn v-else outline dense size="sm" no-caps label="Acknowledge" @click="acknowledge">
      <q-tooltip>Mark this finding as deliberate so it stops blocking</q-tooltip>
    </q-btn>
  </template>
</template>
