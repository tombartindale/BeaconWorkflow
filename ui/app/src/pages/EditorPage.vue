<script setup lang="ts">
// Script editor: edit topic.md (or topic.zh.md) in the browser, with the problems bcn finds
// shown beside it as you type. bcn edit does all checking and writing: --dry-run to check,
// a job to save. Saving is refused if the file changed on disk since it was loaded.
//
// Keys: ⌘/Ctrl-S save · ⌘/Ctrl-Enter check now · Tab indents.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue';
import { useRouter } from 'vue-router';
import type { BcnEditEnvelope, Diagnostic, JobSummary, TopicSourceResponse } from '@beacon/shared';
import { api } from '@/api';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import { confirm } from '@/composables/confirm';
import { LEVEL_ORDER, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

const LINE_PX = 21;          // must match .ed-text line-height in app.css
const CHECK_DELAY_MS = 700;

const props = defineProps<{ id: string; lang: 'en' | 'zh'; startLine: number | null }>();
const beacon = useBeacon();
const router = useRouter();
const draftKey = `beacon-edit-${props.id}-${props.lang}`;
const file = props.lang === 'zh' ? 'topic.zh.md' : 'topic.md';
type Summary = BcnEditEnvelope['results'][number];

const base = shallowRef<TopicSourceResponse>({ text: '', sha256: null, exists: false, path: '' });  // what is on disk
const text = ref('');
const problems = shallowRef<Diagnostic[]>([]);
const summary = shallowRef<Summary | null>(null);
const checking = ref(false);
const saving = ref(false);
const loaded = ref(false);
const textarea = ref<HTMLTextAreaElement | null>(null);
const measure = ref<HTMLDivElement | null>(null);
const gutter = ref<HTMLDivElement | null>(null);
const gutterLines = shallowRef<Array<{ height: number; level: string }>>([]);
let lineTops: number[] = [];
let checkSeq = 0;
let checkTimer: ReturnType<typeof setTimeout> | undefined;
let frame = 0;
let disposed = false;

const dirty = computed(() => text.value !== base.value.text);
const shown = computed(() => problems.value.filter((p) => p.code !== 'EDIT_SAVED')
  .sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level] || (a.line || 0) - (b.line || 0)));
const status = computed(() => {
  const errs = problems.value.filter((p) => p.level === 'error').length;
  const warns = problems.value.filter((p) => p.level === 'warn').length;
  return [dirty.value ? 'Unsaved changes' : 'Saved', checking.value ? 'checking…' : null,
    summary.value ? `${plural(errs, 'error')} · ${plural(warns, 'warning')}` : null].filter(Boolean).join(' · ');
});
const counts = computed(() => {
  const s = summary.value as (Summary & { slides?: number; words?: number; target_words?: number }) | null;
  if (!s) return '';
  return [s.slides != null ? `${s.slides} slides` : null,
    s.words != null && props.lang === 'en' ? `${s.words} words${s.target_words ? ` / ${s.target_words} target` : ''}` : null].filter(Boolean).join(' · ');
});

// Long narration lines wrap, so each gutter number is given the height its line actually
// takes, measured in a hidden copy of the text laid out at the same width.
function drawGutter() {
  const ta = textarea.value, m = measure.value;
  if (!ta || !m) return;
  const lines = text.value.split('\n');
  m.style.width = `${ta.clientWidth}px`;
  m.replaceChildren(...lines.map((l) => { const d = document.createElement('div'); d.textContent = l || ' '; return d; }));
  const heights = [...m.children].map((d) => d.getBoundingClientRect().height || LINE_PX);
  lineTops = [];
  let top = 0;
  for (const h of heights) { lineTops.push(top); top += h; }
  const byLine = new Map<number, string>();
  for (const p of problems.value) if (p.line) byLine.set(p.line, byLine.get(p.line) === 'error' || p.level === 'error' ? 'error' : p.level);
  gutterLines.value = heights.map((height, i) => ({ height, level: byLine.get(i + 1) || '' }));
  void nextTick(() => { if (gutter.value) gutter.value.scrollTop = ta.scrollTop; });
}
const redrawSoon = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(drawGutter); };
const resizeObs = new ResizeObserver(redrawSoon);

function jumpTo(line: number) {
  const ta = textarea.value;
  if (!ta) return;
  const lines = text.value.split('\n');
  const l = Math.max(1, Math.min(line, lines.length));
  let start = 0;
  for (let i = 0; i < l - 1; i++) start += lines[i].length + 1;
  ta.focus();
  ta.setSelectionRange(start, start + lines[l - 1].length);
  ta.scrollTop = Math.max(0, (lineTops[l - 1] ?? (l - 1) * LINE_PX) - 5 * LINE_PX);
}

async function check() {
  clearTimeout(checkTimer);
  const seq = ++checkSeq;
  checking.value = true;
  try {
    const env = await api<BcnEditEnvelope>(`/api/topic/${props.id}/check`, { body: { text: text.value, lang: props.lang } });
    if (seq !== checkSeq || disposed) return;   // a newer check is on its way
    problems.value = env.diagnostics as Diagnostic[];
    summary.value = env.results?.[0] ?? null;
  } catch (e) {
    if (seq === checkSeq) beacon.toast(`Could not check: ${(e as Error).message}`, true);
  }
  if (seq === checkSeq) checking.value = false;
  drawGutter();
}

function onInput() {
  redrawSoon();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(check, CHECK_DELAY_MS);
  try { sessionStorage.setItem(draftKey, JSON.stringify({ text: text.value, sha256: base.value.sha256 })); } catch { /* fine */ }
}

async function save(overwrite = false): Promise<void> {
  if (saving.value) return;
  saving.value = true;
  try {
    const job = await api<JobSummary>(`/api/topic/${props.id}/save`, { body: { text: text.value, lang: props.lang, expect_sha: base.value.sha256, overwrite } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env?.diagnostics.some((d) => d.code === 'EDIT_CONFLICT')) {
      saving.value = false;
      const ok = await confirm({
        title: 'Overwrite the newer version?', ok: 'Overwrite', danger: true,
        lines: [`${base.value.path} was changed on disk after you opened it (a sync pull or another editor).`,
          'Overwrite replaces that version with yours; the replaced version is kept in the topic\'s .history folder. Or cancel, copy what you need, and reload the page to see the new version.'],
      });
      if (ok) return save(true);
      return;
    }
    if (!env || done.state !== 'done') throw new Error('the save job failed; see Jobs');
    const r = (env.results?.[0] || {}) as { sha256?: string; written?: boolean };
    base.value = { ...base.value, text: text.value, sha256: r.sha256 || base.value.sha256, exists: true };
    try { sessionStorage.removeItem(draftKey); } catch { /* fine */ }
    problems.value = env.diagnostics;
    summary.value = (env.results?.[0] as Summary) ?? null;
    const errs = problems.value.filter((p) => p.level === 'error').length;
    beacon.toast(r.written === false ? 'No changes to save.' : `Saved${errs ? ` · ${plural(errs, 'problem')} left` : ' · no problems'}`);
  } catch (e) {
    beacon.toast(`Not saved: ${(e as Error).message}`, true);
  }
  saving.value = false;
  drawGutter();
}

async function revert() {
  if (dirty.value && !(await confirm({ title: 'Discard your changes?', lines: ['The editor goes back to the version on disk.'], ok: 'Discard', danger: true }))) return;
  text.value = base.value.text;
  try { sessionStorage.removeItem(draftKey); } catch { /* fine */ }
  await nextTick();
  drawGutter();
  void check();
}

function onTab(e: KeyboardEvent) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  e.preventDefault();
  const ta = textarea.value!;
  ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end');
  text.value = ta.value;
  onInput();
}
function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty.value) void save(); }
  else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void check(); }
}
function onUnload(e: BeforeUnloadEvent) { if (dirty.value) { e.preventDefault(); e.returnValue = ''; } }

onMounted(async () => {
  document.addEventListener('keydown', onKey);
  window.addEventListener('beforeunload', onUnload);
  try { base.value = await api<TopicSourceResponse>(`/api/topic/${props.id}/source?lang=${props.lang}`); } catch (e) { beacon.toast((e as Error).message, true); return; }
  if (disposed) return;
  let recovered: { text: string; sha256: string | null } | null = null;
  try { recovered = JSON.parse(sessionStorage.getItem(draftKey) || 'null'); } catch { /* fine */ }
  if (recovered && recovered.text !== base.value.text && recovered.sha256 === base.value.sha256) {
    text.value = recovered.text;
    beacon.toast('Recovered your unsaved changes from before.');
  } else {
    text.value = base.value.text;
  }
  loaded.value = true;
  await nextTick();
  if (textarea.value) resizeObs.observe(textarea.value);
  drawGutter();
  await check();
  if (props.startLine) jumpTo(props.startLine); else textarea.value?.focus();
});

onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(checkTimer);
  cancelAnimationFrame(frame);
  resizeObs.disconnect();
  document.removeEventListener('keydown', onKey);
  window.removeEventListener('beforeunload', onUnload);
  if (dirty.value) beacon.toast('Unsaved changes are kept in this tab: reopen the editor to carry on.');
});
</script>

<template>
  <q-page padding>
    <PageHeader :title="`Edit ${file}`" :sub="status"
      :crumbs="[{ label: 'Programme', to: '/' }, { label: id.slice(0, 6), to: `/module/${id.slice(0, 6)}` }, { label: id, to: `/topic/${id}` }]">
      <q-btn-toggle :model-value="lang" dense no-caps unelevated toggle-color="primary"
        :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]"
        @update:model-value="(l) => router.push(`/edit/${id}?lang=${l}`)" />
      <q-btn outline no-caps label="Check now" @click="check"><q-tooltip>⌘/Ctrl-Enter</q-tooltip></q-btn>
      <q-btn outline no-caps label="Revert" @click="revert" />
      <q-btn unelevated color="primary" no-caps :label="saving ? 'Saving…' : 'Save'" :disable="saving || !dirty" @click="save()"><q-tooltip>⌘/Ctrl-S</q-tooltip></q-btn>
      <q-btn flat no-caps label="Back to topic" :to="`/topic/${id}`" />
    </PageHeader>
    <div class="row q-col-gutter-md">
      <div class="col-12 col-lg-8">
        <q-card flat bordered>
          <div class="ed-box">
            <div ref="gutter" class="ed-gutter" aria-hidden="true">
              <div v-for="(g, i) in gutterLines" :key="i" :class="['ln', g.level]" :style="{ height: `${g.height}px` }">{{ i + 1 }}</div>
              <div :style="{ height: `${textarea?.clientHeight || 0}px` }"></div>
            </div>
            <textarea ref="textarea" v-model="text" class="ed-text" spellcheck="true" autocomplete="off" aria-label="Script"
              :placeholder="loaded && !base.exists ? `${file} does not exist yet. Paste or write the script here and save to create it.` : ''"
              @input="onInput" @scroll="gutter && (gutter.scrollTop = textarea!.scrollTop)" @keydown.tab="onTab"></textarea>
            <div ref="measure" class="ed-measure" aria-hidden="true"></div>
          </div>
          <q-card-section class="text-caption text-grey-7 q-py-sm">⌘S save · ⌘↵ check · slides are separated by a line of ---; narration goes in a final &gt; **Say:** block</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-lg-4">
        <q-card flat bordered>
          <q-card-section class="row items-center q-pb-sm">
            <div class="text-subtitle1 text-weight-medium">Problems</div>
            <q-space /><span class="text-caption text-grey-7">{{ counts }}</span>
          </q-card-section>
          <q-list v-if="shown.length" separator>
            <DiagnosticItem v-for="(p, i) in shown" :key="i" :d="p">
              <template #message>
                <a v-if="p.line" href="#" @click.prevent="jumpTo(p.line)">line {{ p.line }}: </a>{{ p.message }}{{ p.data?.acknowledged ? ' (acknowledged)' : '' }}
              </template>
            </DiagnosticItem>
          </q-list>
          <q-card-section v-else class="text-grey-7">{{ summary ? 'No problems.' : 'Checking…' }}</q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
