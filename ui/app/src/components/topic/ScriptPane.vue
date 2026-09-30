<script setup lang="ts">
// The script, slide by slide, with the narration under each slide and word counts against
// the target for the topic's minutes.
import { computed, inject, ref } from 'vue';
import { fileUrl } from '@/api';
import StateChip from '@/components/StateChip.vue';
import { TOPIC } from './context';

const t = inject(TOPIC)!;
const lang = ref<'en' | 'zh'>('en');
const s = computed(() => t.show.value?.[lang.value] ?? null);
const LABEL: Record<string, string> = { pdf: 'PDF', html: 'for teleprompter', txt: 'text' };
const scriptFiles = computed(() => (t.status.value?.artifacts || []).filter((a) => a.kind === 'script' && a.exists));
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center gap-sm q-pb-sm">
      <div class="text-subtitle1 text-weight-medium">Script</div>
      <q-btn flat dense size="sm" no-caps icon="edit" label="Edit" :to="`/edit/${t.id}?lang=${lang}`" />
      <q-space />
      <q-btn-toggle v-if="t.show.value?.zh" v-model="lang" dense no-caps unelevated size="sm" toggle-color="primary"
        :options="[{ label: 'EN', value: 'en' }, { label: 'ZH', value: 'zh' }]" />
    </q-card-section>
    <q-card-section v-if="scriptFiles.length" class="row items-center gap-sm q-py-none text-caption">
      <span>Recording script:</span>
      <a v-for="a in scriptFiles" :key="a.path" :href="fileUrl(a.path, t.stamp(a.path))" target="_blank"
        :download="a.path.endsWith('.pdf') ? undefined : a.path.split('/').pop()">{{ LABEL[a.path.split('.').pop()!] }}</a>
      <StateChip v-if="scriptFiles[0].stale" kind="stale" label="out of date" />
    </q-card-section>
    <q-card-section v-if="!s" class="text-grey-7">{{ lang === 'en' ? 'No topic.md yet.' : 'No topic.zh.md yet.' }}</q-card-section>
    <template v-else>
      <q-card-section v-if="lang === 'en'" class="row items-center gap-sm q-py-sm">
        <span><strong>{{ s.words }}</strong> words</span>
        <span class="text-grey-7">target {{ s.target_words }} ({{ s.range[0] }}–{{ s.range[1] }}) at {{ s.words_per_minute }} wpm</span>
        <StateChip v-if="s.words_ok === false" kind="error" label="outside tolerance" />
        <StateChip v-else kind="ok" label="within tolerance" />
        <span class="text-caption text-grey-7">per slide {{ s.narration_limits[0] }}–{{ s.narration_limits[1] }}</span>
      </q-card-section>
      <q-separator />
      <div class="pane-scroll">
        <div v-for="sl in s.slides" :id="`script-${sl.index}`" :key="sl.index" class="sslide">
          <div class="shead"><span class="n">{{ sl.index }}</span><span class="text-caption text-grey-7">line {{ sl.line }}</span></div>
          <!-- bcn renders the slide's markdown; the UI shows it as given. -->
          <div class="content" v-html="sl.content_html"></div>
          <div v-if="lang === 'en'" class="narration">
            <StateChip class="wc" :kind="sl.words_ok === false ? 'error' : undefined" :label="`${sl.words} w · Σ ${sl.running_words}`" />
            <template v-if="sl.narration">{{ sl.narration }}</template><span v-else class="text-grey-7">(no narration)</span>
          </div>
        </div>
      </div>
    </template>
  </q-card>
</template>
