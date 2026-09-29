<script setup lang="ts">
// Stage as position in the sequence: filled pips so far, empty ones to come.
defineProps<{ index: number; total: number; size?: 'lg' }>();
</script>

<template>
  <span :class="['pips', size]" role="img" :aria-label="`stage ${index + 1} of ${total}`"><i
    v-for="i in total" :key="i" :class="{ on: i - 1 <= index }"></i></span>
</template>
