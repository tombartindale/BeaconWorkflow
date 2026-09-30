// The worker's half of the job queue: dequeues 'queued' rows from Postgres, runs bcn for
// each target, and writes progress/results back to the same table. Any number of workers
// (even on different hosts) can run this against the same DATABASE_URL/REDIS_URL safely:
// dequeuing uses `SELECT ... FOR UPDATE SKIP LOCKED` so two workers never pick up the same
// job, and the overlap check considers every currently-running row, not just this worker's
// own, so two workers never run overlapping targets either.
//
// This is a direct port of the single-process JobQueue.run()/invoke()/schedule() logic
// that used to live in jobs.ts, before the API and the job runner became separate
// processes/containers.
import { spawn, type ChildProcess } from 'node:child_process';
import { constants } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import type { AnyEnvelope, BcnEvent, JobArgs, Prefs } from '@beacon/shared';
import type { Bcn } from './bcn.js';
import type { EventBus } from './bus.js';
import type { DB, JobRow } from './db.js';
import { FLAG_ARGS, overlaps, PIPELINE, summaryFromRow, VALUE_ORDER } from './jobs.js';
import { now, pyJson, sleep } from './util.js';

export const LOG_LINES = 400;
const POLL_MS = 2000;
const HEARTBEAT_MS = 5000;
// A worker missing this many heartbeats in a row is presumed dead; its running jobs are
// reclaimed. Generous relative to HEARTBEAT_MS so a slow GC pause never falsely reaps a
// job that is, in fact, still running fine.
const HEARTBEAT_DEAD_MS = HEARTBEAT_MS * 6;

interface RunningJob {
  id: number;
  targets: string[];
  targetIndex: number;
  cancelRequested: boolean;
  proc: ChildProcess | null;
  log: string[];
  envelopes: AnyEnvelope[];
  t0: number;
}

export class Scheduler {
  readonly workerId = randomUUID();
  private running = new Map<number, RunningJob>();
  private wakeRequested = false;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private stopped = false;

  constructor(private db: DB, private bus: EventBus, private bcn: Bcn, private root: string,
    private prefs: () => Promise<Prefs>) {}

  start(): void {
    this.bus.subscribe((ev) => { if (ev.type === 'job-wake') this.wakeRequested = true; });
    void this.heartbeat();
    this.heartbeatTimer = setInterval(() => void this.heartbeat(), HEARTBEAT_MS);
    void this.reapAbandoned();
    void this.loop();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    for (const j of this.running.values()) if (j.proc && j.proc.exitCode === null) j.proc.kill('SIGINT');
    // Let bcn's own SIGINT handling clean up partial output before this process exits.
    const deadline = Date.now() + 10_000;
    while (this.running.size && Date.now() < deadline) await sleep(200);
    await this.db.pool.query('DELETE FROM worker_heartbeats WHERE worker_id=$1', [this.workerId]);
  }

  private async heartbeat(): Promise<void> {
    await this.db.pool.query(
      'INSERT INTO worker_heartbeats(worker_id, last_seen) VALUES($1, $2) ON CONFLICT (worker_id) DO UPDATE SET last_seen=excluded.last_seen',
      [this.workerId, now()]);
  }

  /** Jobs left 'running' by a worker that stopped heartbeating (crashed, killed, OOM) are
   *  marked interrupted, the same way a restarted single-process backend used to recover
   *  its own crashed jobs — except this also covers a *different* worker process dying. */
  private async reapAbandoned(): Promise<void> {
    await this.db.pool.query(
      `UPDATE jobs SET state='interrupted', finished=$1, worker_id=NULL
       WHERE state='running' AND (
         worker_id IS NULL
         OR worker_id NOT IN (SELECT worker_id FROM worker_heartbeats)
         OR worker_id IN (SELECT worker_id FROM worker_heartbeats WHERE last_seen < $2)
       )`,
      [now(), now(new Date(Date.now() - HEARTBEAT_DEAD_MS))]);
  }

  private async loop(): Promise<void> {
    while (!this.stopped) {
      this.wakeRequested = false;
      try {
        await this.tick();
      } catch (e) {
        // A single bad tick (e.g. a transient DB hiccup) must not kill the worker process.
        process.stderr.write(`scheduler: tick failed: ${(e as Error).message}\n`);
      }
      await sleep(POLL_MS);
      // A wake signal during the sleep is still honoured on the very next loop iteration,
      // since wakeRequested just makes the wait feel shorter in the common case.
    }
  }

  private async tick(): Promise<void> {
    const prefs = await this.prefs();
    const limit = Math.max(1, Math.trunc(Number(prefs.parallel_jobs) || 2));
    if (this.running.size >= limit) return;

    const client = await this.db.pool.connect();
    let row: JobRow | undefined;
    try {
      await client.query('BEGIN');
      // Every currently-running row (from any worker) counts toward the overlap check, not
      // just this process's own `this.running` map, so two worker processes never touch the
      // same topic even though neither can see the other's in-memory state.
      const busyRes = await client.query<{ targets: string }>("SELECT targets FROM jobs WHERE state='running'");
      const busyTargets = busyRes.rows.flatMap((r) => JSON.parse(r.targets) as string[]);
      const queuedRes = await client.query<JobRow>(
        "SELECT * FROM jobs WHERE state='queued' ORDER BY id FOR UPDATE SKIP LOCKED");
      row = queuedRes.rows.find((r) => {
        const targets = JSON.parse(r.targets) as string[];
        return !targets.some((a) => busyTargets.some((b) => overlaps(a, b)));
      });
      if (row) {
        await client.query("UPDATE jobs SET state='running', started=$1, worker_id=$2 WHERE id=$3", [now(), this.workerId, row.id]);
      }
      await client.query('COMMIT');
    } catch (e) {
      await client.query('ROLLBACK');
      throw e;
    } finally {
      client.release();
    }

    if (row) {
      const summary = summaryFromRow({ ...row, state: 'running', started: now(), worker_id: this.workerId });
      this.bus.publish({ type: 'job', job: summary });
      void this.run(row);
      // Immediately look for more capacity, in case parallel_jobs > 1 and several jobs
      // with disjoint targets are queued at once.
      void this.tick();
    }
  }

  private async run(row: JobRow): Promise<void> {
    const targets: string[] = JSON.parse(row.targets);
    const args: JobArgs = JSON.parse(row.args);
    const job: RunningJob = { id: row.id, targets, targetIndex: 0, cancelRequested: false, proc: null, log: [], envelopes: [], t0: Date.now() };
    this.running.set(row.id, job);
    const codes: number[] = [];
    try {
      for (let i = 0; i < targets.length; i++) {
        if (await this.cancelRequested(row.id)) { job.cancelRequested = true; break; }
        job.targetIndex = i;
        await this.db.pool.query('UPDATE jobs SET target_index=$1 WHERE id=$2', [i, row.id]);
        const [code, envelope] = await this.invoke(job, row.command, args, targets[i]);
        codes.push(code);
        if (envelope) job.envelopes.push(envelope);
      }
    } catch (e) {
      job.log.push(pyJson({ event: 'log', level: 'error', message: `worker error: ${(e as Error).message}` }));
      codes.push(-1);
    }
    const finished = now();
    const durationMs = Date.now() - job.t0;
    const exitCode = codes.length ? (codes.find((c) => c !== 0) ?? 0) : null;
    const state = job.cancelRequested ? 'cancelled' : (codes.length && codes.every((c) => c === 0) ? 'done' : 'failed');
    await this.db.pool.query(
      `UPDATE jobs SET finished=$1, state=$2, exit_code=$3, duration_ms=$4, envelope=$5, log=$6, worker_id=NULL WHERE id=$7`,
      [finished, state, exitCode, durationMs, job.envelopes.length ? JSON.stringify(job.envelopes) : null,
        this.trimmedLog(job.log).join('\n'), row.id]);
    this.running.delete(row.id);
    const updated = await this.db.pool.query<JobRow>('SELECT * FROM jobs WHERE id=$1', [row.id]);
    if (updated.rows[0]) this.bus.publish({ type: 'job', job: summaryFromRow(updated.rows[0]) });
    void this.tick();
  }

  private async cancelRequested(id: number): Promise<boolean> {
    const res = await this.db.pool.query<{ cancel_requested: boolean }>('SELECT cancel_requested FROM jobs WHERE id=$1', [id]);
    return res.rows[0]?.cancel_requested ?? false;
  }

  private trimmedLog(log: string[]): string[] {
    return log.length > LOG_LINES ? log.slice(log.length - LOG_LINES) : log;
  }

  private argv(command: string, args: JobArgs, target: string): string[] {
    const path = target !== '.' ? join(this.root, target) : this.root;
    const argv = [command, path];
    for (const k of VALUE_ORDER) {
      if (k in args && args[k] !== null && args[k] !== '') argv.push(`--${k.replace(/_/g, '-')}`, String(args[k]));
    }
    const only = Array.isArray(args.only) ? args.only : args.only ? [args.only] : [];
    for (const p of only) argv.push('--only', String(p));
    if ('import' in args) argv.push('--import', String(args.import));
    for (const k of FLAG_ARGS) if (args[k]) argv.push(`--${k.replace(/_/g, '-')}`);
    return argv;
  }

  private async invoke(job: RunningJob, command: string, args: JobArgs, target: string): Promise<[number, AnyEnvelope | null]> {
    const argv = this.argv(command, args, target);
    if (PIPELINE.has(command)) {
      const prefs = await this.prefs();
      argv.push('--jobs', String(Math.max(1, Math.trunc(Number(prefs.jobs) || 1))));
    }
    const [file, ...prefixRest] = this.bcn.prefix;
    job.log.push(pyJson({ event: 'log', level: 'info', message: `$ bcn ${argv.join(' ')}` }));
    return new Promise((resolve, reject) => {
      const p = spawn(file, [...prefixRest, ...argv], { cwd: this.root, stdio: ['ignore', 'pipe', 'pipe'] });
      job.proc = p;
      const out: Buffer[] = [];
      p.stdout.on('data', (b: Buffer) => out.push(b));
      const lines = createInterface({ input: p.stderr });
      lines.on('line', (line) => {
        if (!line) return;
        job.log.push(line);
        let ev: BcnEvent;
        try { ev = JSON.parse(line); } catch { ev = { event: 'log', level: 'info', message: line }; }
        if (ev.event === 'progress') {
          void this.db.pool.query('UPDATE jobs SET progress=$1, last_event=$2 WHERE id=$3', [JSON.stringify(ev), now(), job.id]);
        } else {
          void this.db.pool.query('UPDATE jobs SET last_event=$1 WHERE id=$2', [now(), job.id]);
        }
        this.bus.publish({ type: 'job-event', job: job.id, target_index: job.targetIndex, target_count: job.targets.length, event: ev });
      });
      p.on('error', reject);
      p.on('close', (code, signal) => {
        job.proc = null;
        const text = Buffer.concat(out).toString('utf8');
        let env: AnyEnvelope | null = null;
        if (text.trim()) {
          try { env = JSON.parse(text); } catch {
            job.log.push(pyJson({ event: 'log', level: 'error', message: 'bcn printed no envelope' }));
          }
        }
        resolve([code ?? (signal ? -(constants.signals[signal] ?? 1) : -1), env]);
      });
    });
  }
}
