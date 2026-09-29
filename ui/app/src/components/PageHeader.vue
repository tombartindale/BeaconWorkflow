<script setup lang="ts">
// Crumbs, a title, a line under it, and the page's own actions on the right.
defineProps<{ title: string; sub?: string; crumbs?: Array<{ label: string; to?: string }> }>();
</script>

<template>
  <div class="row items-end q-col-gutter-md q-mb-md no-wrap">
    <div class="col">
      <div v-if="crumbs?.length" class="crumbs q-mb-xs">
        <template v-for="(c, i) in crumbs" :key="i">
          <router-link v-if="c.to" :to="c.to">{{ c.label }}</router-link><span v-else>{{ c.label }}</span>
          <span v-if="i < crumbs.length - 1" class="q-mx-xs text-grey-6">/</span>
        </template>
      </div>
      <div class="text-h5 text-weight-medium">{{ title }}</div>
      <div v-if="sub || $slots.sub" class="text-grey-7 q-mt-xs"><slot name="sub">{{ sub }}</slot></div>
    </div>
    <div class="col-auto row items-center q-gutter-sm"><slot /></div>
  </div>
</template>
