<script setup lang="ts">
// Topic: everything about one topic on one page. Script, slides and video panes, then
// artefacts and diagnostics, with actions to run any step.
import { computed, onBeforeUnmount, provide, ref, shallowRef, watch } from 'vue';
import type { Diagnostic, DiagnosticsResponse, TopicResponse, TopicVerifyResponse } from '@beacon/shared';
import { api } from '@/api';
import ArtefactsTable from '@/components/topic/ArtefactsTable.vue';
import { TOPIC } from '@/components/topic/context';
import ScriptPane from '@/components/topic/ScriptPane.vue';
import SlidesPane from '@/components/topic/SlidesPane.vue';
import TopicActions from '@/components/topic/TopicActions.vue';
import TopicDiagnostics from '@/components/topic/TopicDiagnostics.vue';
import TopicHead from '@/components/topic/TopicHead.vue';
import VideoPane from '@/components/topic/VideoPane.vue';
import { topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const props = defineProps<{ id: string; startAt: number | null }>();
const beacon = useBeacon();
const rel = topicPath(props.id);
const data = shallowRef<TopicResponse | null>(null);
const diags = ref<Diagnostic[]>([]);
const verify = shallowRef<TopicVerifyResponse | null>(null);
const verifying = ref(false);
const error = ref<string | null>(null);
const layout = ref<'side' | 'stacked'>(beacon.boot?.prefs.topic_layout || 'side');

const show = computed(() => data.value?.show.results?.[0] ?? null);
const status = computed(() => data.value?.status ?? null);

// Cache-bust each URL with its own file's mtime, so media reloads only when that file changes.
function stamp(path: string) {
  const a = (status.value?.artifacts || []).find((x) => x.path === path || (x.kind === 'slides' && path.startsWith(`${x.path}/`)));
  return a?.mtime ?? '0';
}

async function load() {
  try {
    const [t, d] = await Promise.all([
      api<TopicResponse>(`/api/topic/${props.id}`),
      api<DiagnosticsResponse>(`/api/diagnostics?path=${encodeURIComponent(rel)}`),
    ]);
    data.value = t;
    diags.value = d.outstanding || [];
    error.value = null;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function runVerify() {
  verify.value = null;
  verifying.value = true;
  try { verify.value = await api<TopicVerifyResponse>(`/api/topic/${props.id}?verify=1`); } catch (e) { beacon.toast((e as Error).message, true); }
  verifying.value = false;
}

provide(TOPIC, { id: props.id, rel, show, status, stamp, reload: load });

void load().then(runVerify);
let timer: ReturnType<typeof setTimeout> | undefined;
watch(() => beacon.status, () => { clearTimeout(timer); timer = setTimeout(load, 200); });
onBeforeUnmount(() => clearTimeout(timer));

const allDiags = computed(() => [...((verify.value?.diagnostics || []) as Diagnostic[]), ...diags.value]);
</script>

<template>
  <q-page padding>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>
    <div v-if="!data && !error" class="text-grey-7 q-pa-lg"><q-spinner /> Loading {{ id }}…</div>
    <template v-if="data">
      <TopicHead v-model:layout="layout" />
      <TopicActions @verify="runVerify" />
      <div :class="['panes', layout, 'q-mb-md']">
        <ScriptPane />
        <SlidesPane />
        <VideoPane :start-at="startAt" />
      </div>
      <div class="row q-col-gutter-md">
        <div class="col-12 col-lg-6"><TopicDiagnostics :diags="allDiags" /></div>
        <div class="col-12 col-lg-6"><ArtefactsTable :verify="verify" :verifying="verifying" /></div>
      </div>
    </template>
  </q-page>
</template>
