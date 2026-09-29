// Everything the backend has told us: boot information, programme status and jobs, kept
// current by the live event stream. The UI renders this and computes nothing about the
// pipeline itself (spec §1).
import { defineStore } from 'pinia';
import { computed, ref, shallowRef, watch } from 'vue';
import { Notify } from 'quasar';
import type {
  BcnEvent, BootResponse, CodeInfo, JobArgs, JobDetail, JobsResponse, JobSummary, ServerEvent, StatusResponse,
} from '@beacon/shared';
import { api } from '@/api';

export const LIVE_STATES = ['queued', 'running', 'stalled'];
export type JobEventListener = (ev: { job: number; event: BcnEvent }) => void;

export const useBeacon = defineStore('beacon', () => {
  const boot = ref<BootResponse | null>(null);
  const bootError = ref<string | null>(null);
  const status = shallowRef<StatusResponse | null>(null);
  const jobs = ref(new Map<number, JobSummary & { _seen?: number }>());
  const codes = computed(() => new Map<string, CodeInfo>((boot.value?.codes || []).map((c) => [c.code, c])));
  const live = ref<'connecting' | 'up' | 'down'>('connecting');
  const now = ref(Date.now());  // ticks every 2 s: stalled detection is time-based
  const jobEventListeners = new Set<JobEventListener>();

  const activeJobs = computed(() => [...jobs.value.values()].filter((j) => LIVE_STATES.includes(j.state)).length);

  async function loadBoot() {
    try {
      boot.value = await api<BootResponse>('/api/boot');
    } catch (e) {
      bootError.value = (e as Error).message;
    }
  }

  async function loadStatus() {
    status.value = await api<StatusResponse>('/api/status');
  }

  async function loadJobs() {
    const r = await api<JobsResponse>('/api/jobs?limit=150');
    jobs.value = new Map(r.jobs.map((j) => [j.id, j]));
  }

  function toast(message: string, error = false) {
    Notify.create(error ? { message, type: 'negative', timeout: 7000 } : { message, color: 'secondary', timeout: 3500 });
  }

  async function runJob(command: string, targets: string[], args: JobArgs = {}): Promise<JobSummary> {
    try {
      const job = await api<JobSummary>('/api/jobs', { body: { command, targets, args } });
      jobs.value.set(job.id, job);
      toast(`Queued: ${job.label}`);
      return job;
    } catch (e) {
      toast((e as Error).message, true);
      throw e;
    }
  }

  /** Track a job created elsewhere (intake, save, import) so awaitJob sees it. */
  function trackJob(job: JobSummary) { jobs.value.set(job.id, job); }

  /** Wait for a job to finish, resolving with its full record (envelopes included). */
  function awaitJob(id: number): Promise<JobDetail> {
    return new Promise((resolve, reject) => {
      let finished = false;
      let stop: (() => void) | null = null;
      stop = watch(() => jobs.value.get(id)?.state, (state) => {
        if (finished || !state || LIVE_STATES.includes(state)) return;
        finished = true;
        stop?.();
        api<JobDetail>(`/api/jobs/${id}`).then(resolve, reject);
      }, { immediate: true });
      if (finished) stop();
    });
  }

  function onJobEvent(fn: JobEventListener) {
    jobEventListeners.add(fn);
    return () => { jobEventListeners.delete(fn); };
  }

  let statusTimer: ReturnType<typeof setTimeout> | undefined;
  function connect() {
    const es = new EventSource('/api/events');
    es.addEventListener('hello', () => {
      live.value = 'up';
      void loadStatus().catch(() => {});
      void loadJobs().catch(() => {});
    });
    es.onerror = () => { live.value = 'down'; };
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as ServerEvent;
      if (ev.type === 'status') {
        // Coalesce bursts of refreshes into one reload.
        clearTimeout(statusTimer);
        statusTimer = setTimeout(() => { void loadStatus().catch(() => {}); }, 150);
      } else if (ev.type === 'job') {
        jobs.value.set(ev.job.id, { ...(jobs.value.get(ev.job.id) || {}), ...ev.job });
      } else if (ev.type === 'job-event') {
        const j = jobs.value.get(ev.job);
        if (j) {
          if (ev.event.event === 'progress') j.progress = ev.event as JobSummary['progress'];
          j.target_index = ev.target_index;
          j.last_event_age_ms = 0;
          j._seen = Date.now();
          if (j.state === 'stalled') j.state = 'running';
        }
        for (const fn of [...jobEventListeners]) fn({ job: ev.job, event: ev.event });
      } else if (ev.type === 'warnings') {
        if (boot.value) boot.value.warnings = ev.warnings;
      } else if (ev.type === 'status-error') {
        toast(ev.error, true);
      }
    };
    setInterval(() => { now.value = Date.now(); }, 2000);
  }

  /** A running job that has gone quiet reads as stalled. */
  function stateOf(j: JobSummary & { _seen?: number }) {
    if (j.state === 'running' && j._seen && now.value - j._seen > 10_000) return 'stalled';
    return j.state;
  }

  return {
    boot, bootError, status, jobs, codes, live, now, activeJobs,
    loadBoot, loadStatus, loadJobs, runJob, trackJob, awaitJob, onJobEvent, connect, toast, stateOf,
  };
});
