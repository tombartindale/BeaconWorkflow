<script setup lang="ts">
import { computed } from 'vue';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const NAV = [
  { to: '/', label: 'Programme', icon: 'dashboard' },
  { to: '/diagnostics', label: 'Diagnostics', icon: 'rule' },
  { to: '/jobs', label: 'Jobs', icon: 'play_circle' },
  { to: '/translation', label: 'Translation', icon: 'translate' },
  { to: '/sync', label: 'Sync', icon: 'sync' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
];
const LIVE = {
  connecting: { color: 'grey', label: 'connecting' },
  up: { color: 'positive', label: 'live' },
  down: { color: 'warning', label: 'reconnecting' },
};

const banners = computed(() => {
  const out = (beacon.boot?.warnings || []).map((text) => ({ text, error: false }));
  const doctor = beacon.boot?.doctor;
  for (const r of doctor?.results || []) {
    if (!r.ok) out.push({ text: `Tool check failed for ${r.topic}: ${(doctor?.diagnostics || []).map((d) => d.message).join(' ') || 'see bcn doctor.'}`, error: true });
  }
  return out;
});
</script>

<template>
  <q-layout view="hHh lpR fFf">
    <q-header bordered class="bg-primary text-white">
      <q-toolbar>
        <q-btn flat no-caps to="/" class="text-weight-bold text-subtitle1 q-mr-md" label="NUCS" />
        <q-tabs dense no-caps inline-label shrink stretch class="col" active-color="white" indicator-color="white">
          <q-route-tab v-for="n in NAV" :key="n.to" :to="n.to" :label="n.label" :icon="n.icon">
            <q-badge v-if="n.to === '/jobs' && beacon.activeJobs" color="orange" floating>{{ beacon.activeJobs }}</q-badge>
          </q-route-tab>
        </q-tabs>
        <q-chip dense square :color="LIVE[beacon.live].color" text-color="white" icon="circle" class="live-chip">
          {{ LIVE[beacon.live].label }}
          <q-tooltip>Connection to the backend</q-tooltip>
        </q-chip>
      </q-toolbar>
    </q-header>

    <q-page-container>
      <q-banner v-for="(b, i) in banners" :key="i" dense :class="b.error ? 'bg-negative text-white' : 'bg-orange-2 text-black'">
        <template #avatar><q-icon :name="b.error ? 'error' : 'warning'" /></template>
        {{ b.text }}
      </q-banner>
      <q-page v-if="beacon.bootError" padding>
        <q-banner class="bg-negative text-white" rounded>The backend is not reachable: {{ beacon.bootError }}</q-banner>
      </q-page>
      <router-view v-else v-slot="{ Component, route }">
        <component :is="Component" :key="route.fullPath" />
      </router-view>
    </q-page-container>
  </q-layout>
</template>

<style scoped>
.live-chip :deep(.q-icon) { font-size: 10px; }
</style>
