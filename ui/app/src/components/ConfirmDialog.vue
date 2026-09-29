<script setup lang="ts">
import { ref } from 'vue';
import { useDialogPluginComponent } from 'quasar';

export interface ConfirmLine { text: string; mono?: boolean; muted?: boolean; strong?: string }

const props = defineProps<{
  title: string;
  lines?: Array<string | ConfirmLine>;
  ok?: string;
  prompt?: { placeholder: string };
  danger?: boolean;
}>();
defineEmits([...useDialogPluginComponent.emits]);
const { dialogRef, onDialogHide, onDialogOK, onDialogCancel } = useDialogPluginComponent();
const text = ref('');
const line = (l: string | ConfirmLine): ConfirmLine => (typeof l === 'string' ? { text: l } : l);
const accept = () => onDialogOK(props.prompt ? text.value.trim() : true);
</script>

<template>
  <q-dialog ref="dialogRef" @hide="onDialogHide">
    <q-card style="width: 560px; max-width: 92vw">
      <q-card-section class="text-h6">{{ title }}</q-card-section>
      <q-card-section class="q-pt-none">
        <p v-for="(l, i) in (lines || []).map(line)" :key="i" :class="{ 'text-mono': l.mono, 'text-grey-7': l.muted }">
          <template v-if="l.strong">{{ l.text.split(l.strong)[0] }}<strong>{{ l.strong }}</strong>{{ l.text.split(l.strong).slice(1).join(l.strong) }}</template>
          <template v-else>{{ l.text }}</template>
        </p>
        <q-input v-if="prompt" v-model="text" autofocus dense outlined :placeholder="prompt.placeholder" @keyup.enter="accept" />
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat label="Cancel" @click="onDialogCancel" />
        <q-btn unelevated :color="danger ? 'negative' : 'primary'" :label="ok || 'Run'" :autofocus="!prompt" @click="accept" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>
