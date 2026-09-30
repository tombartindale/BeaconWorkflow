// The job queue's API-facing half: validating and enqueueing jobs, and reading their
// state back. Nothing here runs bcn — that is the worker process's job (see worker.ts
// and scheduler.ts), reached only by writing a 'queued' row to Postgres and publishing a
// wake signal so a worker checks for work immediately instead of waiting for its next
// poll.
//
// This split (API enqueues, a separate worker process dequeues and runs) replaces the
// single-process in-memory queue the UI used before it ran as more than one container:
// the `jobs` table in Postgres is now the only source of truth for job state, so any
// number of API replicas and worker replicas can share it safely.
import type { AnyEnvelope, JobArgs, JobDetail, JobState, JobSummary } from '@beacon/shared';
import type { EventBus } from './bus.js';
import type { DB, JobRow } from './db.js';
import { BadRequest } from './errors.js';
import { now } from './util.js';

export const STALL_MS = 10_000;

// What the browser may ask for. Anything else is refused.
export const COMMANDS = new Set(['validate', 'render', 'script', 'bumpers', 'cues', 'subtitles', 'compose', 'package', 'qa',
  'review', 'intake', 'translation', 'sync', 'ack', 'edit', 'qti', 'coursemap']);
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
// The order value arguments are passed to bcn in. Shared with the worker's argv builder.
export const VALUE_ORDER = ['lang', 'theme', 'set', 'unset', 'item', 'correct', 'from', 'by', 'prefer', 'fingerprint', 'note', 'expect_sha'];
export const PIPELINE = new Set(['validate', 'render', 'script', 'bumpers', 'cues', 'subtitles', 'compose', 'package', 'qa']);

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

/** Reconstructs the JobSummary/JobDetail shape the browser expects from a `jobs` row. */
export function summaryFromRow(row: JobRow): JobSummary {
  const lastEvent = row.last_event ? new Date(row.last_event).getTime() : 0;
  const stalled = row.state === 'running' && lastEvent > 0 && Date.now() - lastEvent > STALL_MS;
  return {
    id: row.id, command: row.command, label: row.label, targets: JSON.parse(row.targets), args: JSON.parse(row.args),
    state: (stalled ? 'stalled' : row.state) as JobState, created: row.created, started: row.started, finished: row.finished,
    by: row.by || '', exit_code: row.exit_code, duration_ms: row.duration_ms,
    progress: row.progress ? JSON.parse(row.progress) : null,
    target_index: row.target_index, target_count: JSON.parse(row.targets).length,
    last_event_age_ms: lastEvent ? Math.floor(Date.now() - lastEvent) : null,
    ok: ['queued', 'running'].includes(row.state) ? null : row.state === 'done',
  };
}

export function detailFromRow(row: JobRow): JobDetail {
  const envelopes: AnyEnvelope[] = row.envelope ? JSON.parse(row.envelope) : [];
  const log = row.log ? row.log.split(/\r?\n/) : [];
  return { ...summaryFromRow(row), log, envelopes };
}

/** Published on the wake channel so a worker checks for work immediately, not on its next poll. */
export const WAKE_CHANNEL = 'beacon:jobs:wake';

export class JobQueue {
  constructor(private db: DB, private bus: EventBus) {}

  async submit(command: string, targets: string[], args: JobArgs, label: string, by: string): Promise<JobSummary> {
    validateArgs(command, targets, args);
    const created = now();
    const res = await this.db.pool.query<{ id: number }>(
      "INSERT INTO jobs(created, by, command, label, targets, args, state) VALUES($1,$2,$3,$4,$5,$6, 'queued') RETURNING id",
      [created, by, command, label, JSON.stringify(targets), JSON.stringify(args)]);
    const row = await this.getRow(res.rows[0].id);
    const summary = summaryFromRow(row!);
    this.publish(summary);
    this.wake();
    return summary;
  }

  /** Queued jobs are simply removed; a running job is asked to stop via its `cancel_requested` flag,
   *  which the worker running it checks between targets and honours by sending bcn SIGINT. */
  async cancel(id: number): Promise<JobSummary | null> {
    const row = await this.getRow(id);
    if (!row) return null;
    if (row.state === 'queued') {
      await this.db.pool.query("UPDATE jobs SET state='cancelled', finished=$1 WHERE id=$2", [now(), id]);
    } else if (row.state === 'running') {
      await this.db.pool.query('UPDATE jobs SET cancel_requested=true WHERE id=$1', [id]);
    } else {
      return summaryFromRow(row);
    }
    const updated = await this.getRow(id);
    const summary = summaryFromRow(updated!);
    this.publish(summary);
    return summary;
  }

  async get(id: number): Promise<JobDetail | null> {
    const row = await this.getRow(id);
    return row ? detailFromRow(row) : null;
  }

  async list(limit = 100): Promise<JobSummary[]> {
    const res = await this.db.pool.query<JobRow>('SELECT * FROM jobs ORDER BY id DESC LIMIT $1', [limit]);
    return res.rows.map(summaryFromRow);
  }

  async running(): Promise<number> {
    const res = await this.db.pool.query<{ count: string }>("SELECT count(*)::int AS count FROM jobs WHERE state='running'");
    return Number(res.rows[0]?.count ?? 0);
  }

  private async getRow(id: number): Promise<JobRow | null> {
    const res = await this.db.pool.query<JobRow>('SELECT * FROM jobs WHERE id=$1', [id]);
    return res.rows[0] ?? null;
  }

  private publish(job: JobSummary): void {
    this.bus.publish({ type: 'job', job });
  }

  /** Tells any worker listening to check for queued work now, instead of on its next poll. */
  private wake(): void {
    this.bus.publish({ type: 'job-wake' });
  }
}
