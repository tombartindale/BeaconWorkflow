// The job queue. Every action is a job; nothing runs synchronously in a request.
//
// A job is one command over one or more target paths, run as one bcn invocation per
// target. Progress is bcn's own NDJSON from stderr, relayed to the browser unchanged with
// the job id attached. The envelope from stdout is stored whole.
//
// Up to `parallel_jobs` jobs run at once, but never two whose targets overlap, so two jobs
// never touch the same topic. Cancelling sends SIGINT so bcn can stop its children and
// remove partial output itself.
import { spawn, type ChildProcess } from 'node:child_process';
import { constants } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import type { AnyEnvelope, BcnEvent, JobArgs, JobDetail, JobState, JobSummary, Prefs, ProgressEvent } from '@beacon/shared';
import type { Bcn } from './bcn.js';
import type { Bus } from './bus.js';
import type { DB, JobRow } from './db.js';
import { BadRequest } from './errors.js';
import { now, pyJson } from './util.js';

export const STALL_MS = 10_000;
export const LOG_LINES = 400;

// What the browser may ask for. Anything else is refused.
export const COMMANDS = new Set(['validate', 'render', 'script', 'bumpers', 'cues', 'subtitles', 'compose', 'package', 'qa',
  'review', 'intake', 'translation', 'sync', 'ack', 'edit', 'qti']);
export const FLAG_ARGS = ['force', 'no_bumpers', 'dump_narration', 'accept', 'clear', 'dry_run', 'export', 'pull', 'push'];
export const VALUE_ARGS: Record<string, RegExp> = {
  lang: /^(en|zh)$/,
  theme: /^[A-Za-z0-9_-]{1,40}$/,
  set: /^\d{1,2}=\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?$/,
  unset: /^\d{1,2}$/,
  item: /^[a-z]{2}-[0-9a-f]{10}$/,
  correct: /^[^\x00-\x1f]{1,200}$/,
  from: /^.{1,1024}$/,      // server-provided paths only, never from the browser
  import: /^.{1,1024}$/,    // checked to be inside the root before use
  by: /^[^\x00-\x1f]{0,80}$/,
  prefer: /^(remote|local)$/,
  expect_sha: /^[0-9a-f]{64}$/,
  fingerprint: /^[A-Z_]{3,40}\|(en|zh)\|s\d{1,3}\|[^\x00-\x1f]{0,300}$/,
  note: /^[^\x00-\x1f]{0,500}$/,
  only: /^[A-Z]{2}\d{4}\/[^\x00-\x1f]{1,300}$/,  // a local path under the root; may be a list
};
// The order value arguments are passed to bcn in.
const VALUE_ORDER = ['lang', 'theme', 'set', 'unset', 'item', 'correct', 'from', 'by', 'prefer', 'fingerprint', 'note', 'expect_sha'];
const PIPELINE = new Set(['validate', 'render', 'script', 'bumpers', 'cues', 'subtitles', 'compose', 'package', 'qa']);

/** Two targets overlap when one contains the other ('.' is the whole programme). */
export function overlaps(a: string, b: string): boolean {
  if (a === '.' || b === '.') return true;
  const pa = a.split('/'), pb = b.split('/');
  const n = Math.min(pa.length, pb.length);
  return pa.slice(0, n).every((x, i) => x === pb[i]);
}

export function validateArgs(command: string, targets: string[], args: JobArgs): void {
  if (!COMMANDS.has(command)) throw new BadRequest(`'${command}' is not a job the UI can run.`);
  if (!targets.length) throw new BadRequest('A job needs at least one target.');
  for (const [k, v] of Object.entries(args)) {
    if (FLAG_ARGS.includes(k)) {
      if (typeof v !== 'boolean') throw new BadRequest(`'${k}' must be true or false.`);
    } else if (k in VALUE_ARGS) {
      const values = k === 'only' && Array.isArray(v) ? v : [v];
      const ok = values.length > 0 && values.every((x) =>
        (typeof x === 'string' || typeof x === 'number') && !String(x).includes('..') && VALUE_ARGS[k].test(String(x)));
      if (!ok) throw new BadRequest(`'${k}' has an invalid value.`);
    } else {
      throw new BadRequest(`'${k}' is not an allowed argument.`);
    }
  }
}

class Job {
  created = now();
  by = '';
  started: string | null = null;
  finished: string | null = null;
  exitCode: number | null = null;
  durationMs: number | null = null;
  envelopes: AnyEnvelope[] = [];
  log: string[] = [];
  progress: ProgressEvent | null = null;
  targetIndex = 0;
  lastEvent = 0;
  cancelRequested = false;
  proc: ChildProcess | null = null;
  t0 = 0;

  constructor(readonly id: number, readonly command: string, readonly label: string, readonly targets: string[],
    readonly args: JobArgs, public state: JobState) {}

  addLog(line: string): void {
    this.log.push(line);
    if (this.log.length > LOG_LINES) this.log.splice(0, this.log.length - LOG_LINES);
  }

  summary(): JobSummary {
    const stalled = this.state === 'running' && this.lastEvent > 0 && Date.now() - this.lastEvent > STALL_MS;
    return {
      id: this.id, command: this.command, label: this.label, targets: this.targets, args: this.args,
      state: stalled ? 'stalled' : this.state, created: this.created, started: this.started, finished: this.finished,
      by: this.by, exit_code: this.exitCode, duration_ms: this.durationMs, progress: this.progress,
      target_index: this.targetIndex, target_count: this.targets.length,
      last_event_age_ms: this.lastEvent ? Math.floor(Date.now() - this.lastEvent) : null,
      ok: ['queued', 'running'].includes(this.state) ? null : this.state === 'done',
    };
  }

  detail(): JobDetail {
    return { ...this.summary(), log: [...this.log], envelopes: this.envelopes };
  }

  static fromRow(row: JobRow): Job {
    const j = new Job(row.id, row.command, row.label, JSON.parse(row.targets), JSON.parse(row.args), row.state as JobState);
    Object.assign(j, { created: row.created, by: row.by || '', started: row.started, finished: row.finished,
      exitCode: row.exit_code, durationMs: row.duration_ms });
    if (row.envelope) j.envelopes = JSON.parse(row.envelope);
    if (row.log) for (const line of row.log.split(/\r?\n/)) j.addLog(line);
    return j;
  }
}

export class JobQueue {
  private jobs = new Map<number, Job>();
  private stallTimer: NodeJS.Timeout | null = null;

  constructor(private db: DB, private bus: Bus, private bcn: Bcn, private root: string,
    private prefs: () => Prefs, private onFinished: (job: JobSummary) => void) {
    this.recover();
  }

  start(): void {
    // Stalled detection is time-based: re-announce quiet running jobs so the browser can show it.
    this.stallTimer = setInterval(() => {
      for (const j of this.jobs.values()) {
        if (j.state === 'running' && j.lastEvent && Date.now() - j.lastEvent > STALL_MS) this.publish(j);
      }
    }, 2000);
    this.schedule();
  }

  stop(): void {
    if (this.stallTimer) clearInterval(this.stallTimer);
    for (const j of this.jobs.values()) if (j.proc && j.proc.exitCode === null) j.proc.kill('SIGINT');
  }

  // -- persistence ------------------------------------------------------------------------------
  /** Running jobs from a previous backend are marked interrupted, never silently lost. */
  private recover(): void {
    this.db.conn.prepare("UPDATE jobs SET state='interrupted', finished=? WHERE state='running'").run(now());
    for (const row of this.db.conn.prepare("SELECT * FROM jobs WHERE state='queued' ORDER BY id").all() as JobRow[]) {
      this.jobs.set(row.id, Job.fromRow(row));
    }
  }

  private save(j: Job): void {
    this.db.conn.prepare('UPDATE jobs SET started=?, finished=?, state=?, exit_code=?, duration_ms=?, envelope=?, log=? WHERE id=?')
      .run(j.started, j.finished, j.state, j.exitCode, j.durationMs, j.envelopes.length ? JSON.stringify(j.envelopes) : null,
        j.log.join('\n'), j.id);
  }

  // -- public -----------------------------------------------------------------------------------
  submit(command: string, targets: string[], args: JobArgs, label: string, by: string): JobSummary {
    validateArgs(command, targets, args);
    const created = now();
    const info = this.db.conn.prepare("INSERT INTO jobs(created, by, command, label, targets, args, state) VALUES(?,?,?,?,?,?, 'queued')")
      .run(created, by, command, label, JSON.stringify(targets), JSON.stringify(args));
    const j = new Job(Number(info.lastInsertRowid), command, label, targets, args, 'queued');
    j.created = created;
    j.by = by;
    this.jobs.set(j.id, j);
    this.publish(j);
    setImmediate(() => this.schedule());
    return j.summary();
  }

  cancel(id: number): JobSummary | null {
    const j = this.jobs.get(id);
    if (!j) return null;
    j.cancelRequested = true;
    if (j.state === 'queued') {
      j.state = 'cancelled';
      j.finished = now();
      this.save(j);
      this.jobs.delete(j.id);
      this.publish(j);
    } else if (j.proc && j.proc.exitCode === null) {
      j.proc.kill('SIGINT');  // bcn cleans up after itself
    }
    return j.summary();
  }

  get(id: number): JobDetail | null {
    const live = this.jobs.get(id);
    if (live) return live.detail();
    const row = this.db.conn.prepare('SELECT * FROM jobs WHERE id=?').get(id) as JobRow | undefined;
    return row ? Job.fromRow(row).detail() : null;
  }

  list(limit = 100): JobSummary[] {
    const live = new Map([...this.jobs.values()].map((j) => [j.id, j.summary()]));
    const rows = this.db.conn.prepare('SELECT * FROM jobs ORDER BY id DESC LIMIT ?').all(limit) as JobRow[];
    const out = rows.map((row) => {
      const s = live.get(row.id);
      live.delete(row.id);
      return s || Job.fromRow(row).summary();
    });
    return [...[...live.values()].sort((a, b) => b.id - a.id), ...out];
  }

  running(): number {
    return [...this.jobs.values()].filter((j) => j.state === 'running').length;
  }

  // -- scheduling -------------------------------------------------------------------------------
  private schedule(): void {
    const limit = Math.max(1, Math.trunc(Number(this.prefs().parallel_jobs) || 2));
    const running = [...this.jobs.values()].filter((j) => j.state === 'running');
    const queued = [...this.jobs.values()].filter((j) => j.state === 'queued').sort((a, b) => a.id - b.id);
    for (const j of queued) {
      if (running.length >= limit) break;
      const busy = running.flatMap((r) => r.targets);
      if (j.targets.some((a) => busy.some((b) => overlaps(a, b)))) continue;
      j.state = 'running';
      running.push(j);
      void this.run(j);
    }
  }

  private argv(j: Job, target: string): string[] {
    const a = j.args;
    const path = target !== '.' ? join(this.root, target) : this.root;
    const argv = [j.command, path];
    for (const k of VALUE_ORDER) {
      if (k in a && a[k] !== null && a[k] !== '') argv.push(`--${k.replace(/_/g, '-')}`, String(a[k]));
    }
    const only = Array.isArray(a.only) ? a.only : a.only ? [a.only] : [];
    for (const p of only) argv.push('--only', String(p));
    if ('import' in a) argv.push('--import', String(a.import));
    for (const k of FLAG_ARGS) if (a[k]) argv.push(`--${k.replace(/_/g, '-')}`);
    if (PIPELINE.has(j.command)) argv.push('--jobs', String(Math.max(1, Math.trunc(Number(this.prefs().jobs) || 1))));
    return argv;
  }

  private async run(j: Job): Promise<void> {
    j.started = now();
    j.t0 = Date.now();
    j.lastEvent = Date.now();
    this.save(j);
    this.publish(j);
    const codes: number[] = [];
    try {
      for (let i = 0; i < j.targets.length; i++) {
        if (j.cancelRequested) break;
        j.targetIndex = i;
        const [code, envelope] = await this.invoke(j, j.targets[i]);
        codes.push(code);
        if (envelope) j.envelopes.push(envelope);
      }
    } catch (e) {
      j.addLog(pyJson({ event: 'log', level: 'error', message: `UI backend error: ${(e as Error).message}` }));
      codes.push(-1);
    }
    j.finished = now();
    j.durationMs = Date.now() - j.t0;
    j.exitCode = codes.length ? (codes.find((c) => c !== 0) ?? 0) : null;
    if (j.cancelRequested) j.state = 'cancelled';
    else j.state = codes.length && codes.every((c) => c === 0) ? 'done' : 'failed';
    this.save(j);
    this.jobs.delete(j.id);
    this.publish(j);
    try { this.onFinished(j.summary()); } catch { /* never let a listener break the queue */ }
    this.schedule();
  }

  private invoke(j: Job, target: string): Promise<[number, AnyEnvelope | null]> {
    const args = this.argv(j, target);
    const [file, ...prefixRest] = this.bcn.prefix;
    j.addLog(pyJson({ event: 'log', level: 'info', message: `$ bcn ${args.join(' ')}` }));
    return new Promise((resolve, reject) => {
      const p = spawn(file, [...prefixRest, ...args], { cwd: this.root, stdio: ['ignore', 'pipe', 'pipe'] });
      j.proc = p;
      const out: Buffer[] = [];
      p.stdout.on('data', (b: Buffer) => out.push(b));
      const lines = createInterface({ input: p.stderr });
      lines.on('line', (line) => {
        if (!line) return;
        j.lastEvent = Date.now();
        j.addLog(line);
        let ev: BcnEvent;
        try { ev = JSON.parse(line); } catch { ev = { event: 'log', level: 'info', message: line }; }
        if (ev.event === 'progress') j.progress = ev as ProgressEvent;
        // Relayed unchanged; the job id and target position ride alongside.
        this.bus.publish({ type: 'job-event', job: j.id, target_index: j.targetIndex, target_count: j.targets.length, event: ev });
      });
      p.on('error', reject);
      p.on('close', (code, signal) => {
        j.proc = null;
        const text = Buffer.concat(out).toString('utf8');
        let env: AnyEnvelope | null = null;
        if (text.trim()) {
          try { env = JSON.parse(text); } catch {
            j.addLog(pyJson({ event: 'log', level: 'error', message: 'bcn printed no envelope' }));
          }
        }
        // Killed by a signal: report it the way Python's Popen does, as a negative code.
        resolve([code ?? (signal ? -(constants.signals[signal] ?? 1) : -1), env]);
      });
    });
  }

  private publish(j: Job): void {
    this.bus.publish({ type: 'job', job: j.summary() });
  }
}
