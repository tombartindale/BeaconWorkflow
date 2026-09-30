<script setup lang="ts">
// A slide full size, English and Mandarin side by side, with the subtitle safe area on demand.
// Keys: ← → navigate · S safe area · Esc close.
import { computed, onBeforeUnmount, onMounted } from 'vue';
import type { ShowRender } from '@beacon/shared';
import { fileUrl } from '@/api';

const props = defineProps<{ render: ShowRender; stamp: (path: string) => string }>();
const index = defineModel<number | null>('index', { required: true });
const safeArea = defineModel<boolean>('safeArea', { required: true });
const n = computed(() => Math.max(props.render.en?.slides?.length || 0, props.render.zh?.slides?.length || 0));
const i = computed(() => index.value ?? 0);
const pct = computed(() => (props.render.theme ? (100 * props.render.theme.safe_bottom) / props.render.theme.height : 20));

const frames = computed(() => (['en', 'zh'] as const).map((lang) => {
  const src = props.render[lang]?.slides?.[i.value];
  return src ? { lang, url: fileUrl(src, props.stamp(src)) } : null;
}).filter((x): x is { lang: 'en' | 'zh'; url: string } => x !== null));
const messages = computed(() => (['en', 'zh'] as const).flatMap((l) =>
  (props.render[l]?.flagged?.[String(i.value + 1)] || []).map((f) => `${l.toUpperCase()}: ${f.message}`)));

const go = (d: number) => { index.value = Math.max(0, Math.min(n.value - 1, i.value + d)); };
const close = () => { index.value = null; };
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowRight') go(1);
  else if (e.key === 'ArrowLeft') go(-1);
  else if (e.key.toLowerCase() === 's') safeArea.value = !safeArea.value;
  else return;
  e.preventDefault();
}
onMounted(() => document.addEventListener('keydown', onKey));
onBeforeUnmount(() => document.removeEventListener('keydown', onKey));
</script>

<template>
  <q-dialog :model-value="true" maximized @hide="close">
    <q-card class="column no-wrap bg-black text-white">
      <q-card-section class="col row items-center justify-center gap-md" style="min-height: 0" @click.self="close">
        <figure v-for="f in frames" :key="f.lang" class="lightbox-frame q-ma-none" :style="{ width: frames.length > 1 ? '48%' : '80%' }">
          <img :src="f.url" :alt="`${f.lang} slide ${i + 1}`" class="lightbox-img">
          <div v-if="safeArea" class="safe-area" :style="{ height: `${pct}%` }"><span>subtitle safe area · {{ render.theme?.safe_bottom }}px</span></div>
          <figcaption class="text-caption q-mt-xs">{{ f.lang === 'en' ? 'English' : 'Mandarin' }} · slide {{ i + 1 }}</figcaption>
        </figure>
      </q-card-section>
      <q-card-section v-if="messages.length" class="q-py-xs text-orange-4"><div v-for="m in messages" :key="m">{{ m }}</div></q-card-section>
      <q-card-actions align="center" class="q-gutter-sm">
        <q-btn outline no-caps icon="chevron_left" label="Prev" @click="go(-1)" />
        <span>{{ i + 1 }} / {{ n }}</span>
        <q-btn outline no-caps icon-right="chevron_right" label="Next" @click="go(1)" />
        <q-btn outline no-caps :label="safeArea ? 'Hide safe area' : 'Show safe area'" @click="safeArea = !safeArea" />
        <span class="text-caption text-grey-5">← → navigate · S safe area · Esc close</span>
        <q-btn flat no-caps icon="close" label="Close" @click="close" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>
