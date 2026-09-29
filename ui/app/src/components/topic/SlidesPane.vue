<script setup lang="ts">
// The rendered slides, one row each in running order (intro, slides, outro), with Mandarin
// beside English at the same index: the fastest way to see Chinese text overflowing a box
// that English fitted.
import { computed, inject, ref } from 'vue';
import type { RenderFlag } from '@beacon/shared';
import { fileUrl } from '@/api';
import SlideLightbox from './SlideLightbox.vue';
import { TOPIC } from './context';

const t = inject(TOPIC)!;
const render = computed(() => t.show.value?.render);
const en = computed(() => render.value?.en?.slides || []);
const zh = computed(() => render.value?.zh?.slides || []);
const n = computed(() => Math.max(en.value.length, zh.value.length));
const open = ref<number | null>(null);
const safeArea = ref(false);

function thumb(lang: 'en' | 'zh', i: number) {
  const src = render.value?.[lang]?.slides?.[i];
  const flags: RenderFlag[] = render.value?.[lang]?.flagged?.[String(i + 1)] || [];
  const level = flags.some((f) => f.level === 'error') ? 'flagged' : flags.length ? 'flagged-warn' : '';
  return { src, flags, level, url: src ? fileUrl(src, t.stamp(src)) : '' };
}

// Intro (title card) and outro (logo card) from bcn bumpers, per language, each with its own still and video.
function bumper(kind: 'intro' | 'outro') {
  const arts = (t.status.value?.artifacts || []).filter((a) => a.kind.startsWith('bumper') && a.exists);
  const isCard = (a: (typeof arts)[number]) => a.kind === 'bumper_card' && a.path.includes(`.${kind}-card.`);
  return (['en', 'zh'] as const).filter((l) => arts.some((a) => a.lang === l && isCard(a))).map((lang) => {
    const mine = arts.filter((a) => a.lang === lang);
    const img = mine.find(isCard)!;
    const video = mine.find((a) => a.path.endsWith(`.${kind}.${lang}.mp4`));
    return { lang, img: fileUrl(img.path, t.stamp(img.path)), video: video ? fileUrl(video.path, t.stamp(video.path)) : null,
      stale: Boolean(img.stale || video?.stale) };
  });
}
const bumpers = computed(() => ({ intro: bumper('intro'), outro: bumper('outro') }));
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center q-pb-sm">
      <div class="text-subtitle1 text-weight-medium">Slides</div>
      <q-space />
      <span v-if="n" class="text-caption text-grey-7">{{ en.length }} EN{{ zh.length ? ` · ${zh.length} ZH` : '' }} · click to enlarge</span>
    </q-card-section>
    <q-separator />
    <div class="pane-scroll">
      <template v-for="kind in (['intro', 'slides', 'outro'] as const)" :key="kind">
        <div v-if="kind !== 'slides' && bumpers[kind].length" class="sslide">
          <div class="shead"><span class="n">◆</span><span class="text-caption text-grey-7">{{ kind === 'intro' ? 'Intro · title' : 'Outro · logo' }}</span></div>
          <div :class="['slide-pair', { two: bumpers[kind].length > 1 }]">
            <div v-for="b in bumpers[kind]" :key="b.lang">
              <div class="thumb">
                <img :src="b.img" :alt="`${b.lang.toUpperCase()} ${kind} title card`" loading="lazy">
                <span class="i">{{ b.lang.toUpperCase() }}</span>
                <q-badge v-if="b.stale" class="flag" color="warning">out of date</q-badge>
              </div>
              <a v-if="b.video" :href="b.video" target="_blank" class="text-caption">▶ play {{ kind }}</a>
            </div>
          </div>
        </div>
        <template v-if="kind === 'slides'">
          <div v-if="!n" class="q-pa-md text-grey-7">Not rendered yet.</div>
          <div v-for="i in n" :id="`slide-${i}`" :key="i" class="sslide">
            <div class="shead"><span class="n">{{ i }}</span><span class="text-caption text-grey-7">{{ t.show.value?.en?.slides?.[i - 1]?.title || '' }}</span></div>
            <div :class="['slide-pair', { two: zh.length }]">
              <template v-for="lang in (zh.length ? ['en', 'zh'] as const : ['en'] as const)" :key="lang">
                <div v-if="!thumb(lang, i - 1).src" class="thumb missing">no {{ lang }} slide {{ i }}</div>
                <div v-else :class="['thumb', thumb(lang, i - 1).level]" @click="open = i - 1">
                  <img :src="thumb(lang, i - 1).url" :alt="`${lang.toUpperCase()} slide ${i}`" loading="lazy">
                  <span class="i">{{ lang.toUpperCase() }} {{ i }}</span>
                  <q-badge v-if="thumb(lang, i - 1).flags.length" class="flag" :color="thumb(lang, i - 1).level === 'flagged' ? 'negative' : 'warning'">overflow</q-badge>
                  <q-tooltip v-if="thumb(lang, i - 1).flags.length">{{ thumb(lang, i - 1).flags.map((f) => f.message).join('\n') }}</q-tooltip>
                </div>
              </template>
            </div>
          </div>
        </template>
      </template>
    </div>
    <SlideLightbox v-if="open !== null && render" v-model:index="open" v-model:safe-area="safeArea" :render="render" :stamp="t.stamp" />
  </q-card>
</template>
