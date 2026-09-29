<script setup lang="ts">
import { computed, inject } from 'vue';
import type { TopicVerifyResponse } from '@beacon/shared';
import { fileUrl } from '@/api';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, fmtBytes } from '@/format';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

const props = defineProps<{ verify: TopicVerifyResponse | null; verifying: boolean }>();
const t = inject(TOPIC)!;
const beacon = useBeacon();
const rows = computed(() => (t.status.value?.artifacts || []).filter((a) => a.exists || a.kind === 'source'));
const verified = computed(() => {
  const m = new Map<string, boolean | undefined>();
  const arts = (props.verify?.results?.[0]?.artifacts || []) as Array<{ key: string; verified?: boolean }>;
  for (const a of arts) m.set(a.key, a.verified);
  return m;
});
const verifyOk = computed(() => props.verify?.results?.[0]?.ok);
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center q-pb-sm">
      <div class="text-subtitle1 text-weight-medium">Artefacts</div>
      <q-space />
      <span v-if="verifying" class="text-caption text-grey-7"><q-spinner size="xs" /> verifying…</span>
      <StateChip v-else-if="verify" :kind="verifyOk ? 'ok' : 'blocked'" :label="verifyOk ? 'verified' : 'verify found problems'" />
    </q-card-section>
    <q-markup-table flat dense wrap-cells>
      <thead><tr><th class="text-left">File</th><th class="text-left">Lang</th><th class="text-left">Modified</th><th class="text-right">Size</th><th class="text-left">State</th></tr></thead>
      <tbody>
        <tr v-for="a in rows" :key="a.key">
          <td class="text-mono">
            <a v-if="a.exists" :href="fileUrl(a.path)" target="_blank">{{ a.key }}</a><span v-else class="text-grey-7">{{ a.key }}</span>
            <span v-if="a.count" class="text-grey-7"> ({{ a.count }})</span>
          </td>
          <td>{{ a.lang?.toUpperCase() || '' }}</td>
          <td class="text-caption">{{ a.exists ? fmtAgo(a.mtime, beacon.now) : 'missing' }}</td>
          <td class="text-caption text-right">{{ fmtBytes(a.bytes) }}</td>
          <td class="q-gutter-xs">
            <StateChip v-if="a.stale" kind="stale" label="stale" />
            <StateChip v-if="a.hydration === 'cloud'" kind="cloud" label="☁ cloud-only" />
            <StateChip v-if="verified.get(a.key) === true" kind="ok" label="ok" />
            <StateChip v-if="verified.get(a.key) === false" kind="blocked" label="broken" />
          </td>
        </tr>
      </tbody>
    </q-markup-table>
  </q-card>
</template>
