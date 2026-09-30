// The job queue (App/JobQueue: submit, cancel, read) and the worker (Scheduler: dequeue,
// run bcn, overlap enforcement) together, against a stand-in bcn whose timing the tests
// control. Job execution lives in Scheduler, not JobQueue, since a worker is now a
// separate process from the API in the real deployment (see src/worker.ts) — tests start
// both against the same DATABASE_URL/root to exercise the whole path end to end.
//
// TODO(postgres): needs DATABASE_URL to point at a real or containerized Postgres
// instance to run - see the TODO in test/harness.ts for how to stand one up locally.
import { cpSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from '../src/app.js';
import { Bcn } from '../src/bcn.js';
import { Bus } from '../src/bus.js';
import { DB } from '../src/db.js';
import { Forbidden } from '../src/errors.js';
import { overlaps } from '../src/jobs.js';
import { Scheduler } from '../src/scheduler.js';
import { sleep } from '../src/util.js';
import { REPO } from './harness.js';

const FAKE = [process.execPath, join(import.meta.dirname, 'fake-bcn.mjs')];

let base: string;
let root: string;
let dataDir: string;
let logFile: string;
const apps: App[] = [];
const schedulers: Scheduler[] = [];

async function makeApp(): Promise<App> {
  const app = await App.create({ root, bcn: FAKE, dataDir });
  apps.push(app);
  return app;
}

/** Starts a worker scheduler sharing the given app's bus/db/root, as a separate worker
 *  process would in the real deployment. */
function startScheduler(app: App): Scheduler {
  const scheduler = new Scheduler(app.db, app.bus, new Bcn(FAKE, app.root), app.root, () => app.db.prefs());
  scheduler.start();
  schedulers.push(scheduler);
  return scheduler;
}
const events = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n') : []);
async function until(check: () => boolean | Promise<boolean>, ms = 10_000) {
  const t = Date.now();
  while (!(await check())) {
    if (Date.now() - t > ms) throw new Error('timed out waiting');
    await sleep(50);
  }
}

beforeEach(() => {
  base = realpathSync(mkdtempSync(join(tmpdir(), 'beacon-jobs-')));
  root = join(base, 'root');
  dataDir = join(base, 'data');
  logFile = join(base, 'bcn.log');
  cpSync(join(REPO, 'example'), root, { recursive: true });
  process.env.FAKE_BCN_LOG = logFile;
  process.env.FAKE_BCN_MS = '1500';
});

afterEach(async () => {
  for (const scheduler of schedulers.splice(0)) { try { await scheduler.stop(); } catch { /* already stopped */ } }
  for (const app of apps.splice(0)) { try { await app.stop(); } catch { /* already stopped */ } }
  rmSync(base, { recursive: true, force: true });
});

describe('overlaps', () => {
  it('treats containment either way as overlap', () => {
    expect(overlaps('.', 'KV7015')).toBe(true);
    expect(overlaps('KV7015', 'KV7015/U01/T01')).toBe(true);
    expect(overlaps('KV7015/U01/T01', 'KV7015/U01')).toBe(true);
    expect(overlaps('KV7015/U01', 'KV7015/U02')).toBe(false);
    expect(overlaps('KV7015/U01/T01', 'KV7015/U01/T02')).toBe(false);
    expect(overlaps('KV7015', 'KV7016')).toBe(false);
  });
});

describe('job queue', () => {
  it('runs jobs in parallel but never two on the same topic', async () => {
    const app = await makeApp();
    await app.db.setPrefs({ parallel_jobs: 2 });
    startScheduler(app);
    const a = await app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = await app.jobs.submit('validate', ['KV7015/U01/T01'], {}, 'b', 't');
    const c = await app.jobs.submit('validate', ['KV7015/U02'], {}, 'c', 't');
    await until(() => events().length >= 2);
    await sleep(200);
    expect((await app.jobs.get(a.id))!.state).toBe('running');
    expect((await app.jobs.get(b.id))!.state).toBe('queued');      // waits for a: same unit
    expect((await app.jobs.get(c.id))!.state).toBe('running');     // different unit: alongside a
    await until(async () => (await app.jobs.get(b.id))!.state === 'done');
    const order = events().filter((e) => e.startsWith('start')).map((e) => e.split(' ')[2]);
    expect(order.indexOf(join(root, 'KV7015/U01/T01'))).toBeGreaterThan(order.indexOf(join(root, 'KV7015/U01')));
    expect(await app.jobs.get(a.id)).toMatchObject({ state: 'done', exit_code: 0, ok: true });
  });

  it('respects the parallel limit', async () => {
    const app = await makeApp();
    await app.db.setPrefs({ parallel_jobs: 1 });
    startScheduler(app);
    const a = await app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = await app.jobs.submit('validate', ['KV7015/U02'], {}, 'b', 't');
    await until(async () => (await app.jobs.get(a.id))!.state === 'running');
    await sleep(300);
    expect((await app.jobs.get(b.id))!.state).toBe('queued');
    await until(async () => (await app.jobs.get(b.id))!.state === 'done');
  });

  it('cancelling a running job sends SIGINT and lets bcn finish its envelope', async () => {
    process.env.FAKE_BCN_MS = '20000';
    const app = await makeApp();
    startScheduler(app);
    const j = await app.jobs.submit('render', ['KV7015/U01/T01'], {}, 'r', 't');
    await until(() => events().some((e) => e.startsWith('start')));
    await app.jobs.cancel(j.id);
    await until(async () => (await app.jobs.get(j.id))!.state === 'cancelled');
    expect(events().some((e) => e.startsWith('sigint render'))).toBe(true);
    const detail = (await app.jobs.get(j.id))!;
    expect(detail.exit_code).toBe(130);
    expect(detail.envelopes[0]).toMatchObject({ cancelled: true });
  });

  it('cancelling a queued job never runs it', async () => {
    const app = await makeApp();
    await app.db.setPrefs({ parallel_jobs: 1 });
    startScheduler(app);
    await app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = await app.jobs.submit('validate', ['KV7015/U02'], {}, 'b', 't');
    expect((await app.jobs.cancel(b.id))!.state).toBe('cancelled');
    await sleep(2500);
    expect(events().some((e) => e.includes('KV7015/U02'))).toBe(false);
  });

  it('streams progress to the bus and records the log', async () => {
    const app = await makeApp();
    const seen: string[] = [];
    const progress: unknown[] = [];
    app.bus.subscribe((ev) => {
      seen.push(ev.type);
      if (ev.type === 'job-event' && ev.event.event === 'progress') progress.push(ev.event);
    });
    startScheduler(app);
    const j = await app.jobs.submit('validate', ['KV7015/U01/T01'], { lang: 'en' }, 'v', 't');
    await until(async () => (await app.jobs.get(j.id))!.state === 'done');
    expect(seen).toContain('job-event');
    const d = (await app.jobs.get(j.id))!;
    expect(d.log[0]).toBe(`{"event": "log", "level": "info", "message": "$ bcn validate ${join(root, 'KV7015/U01/T01')} --lang en --jobs 1"}`);
    // Progress is live only: a finished job is read back from the database without it, as before.
    expect(progress.length).toBeGreaterThan(3);
    expect(d.log.some((l) => l.includes('"progress"'))).toBe(true);
  });

  it('marks a job left running by a crashed worker as interrupted, and picks up queued ones', async () => {
    const connectionString = process.env.DATABASE_URL!;
    const db = new DB(connectionString);
    await db.init();
    const insert = async (targets: string, state: string, workerId: string | null) => {
      const res = await db.pool.query<{ id: number }>(
        "INSERT INTO jobs(created, by, command, label, targets, args, state, worker_id) VALUES('x', 't', 'validate', 'l', $1, '{}', $2, $3) RETURNING id",
        [targets, state, workerId]);
      return res.rows[0].id;
    };
    // No heartbeat row for this worker id at all: indistinguishable from a worker that
    // started a job and then crashed before ever heartbeating.
    const running = await insert('["KV7015/U01"]', 'running', 'a-crashed-worker');
    const queued = await insert('["KV7015/U02"]', 'queued', null);
    await db.close();
    const app = await makeApp();
    startScheduler(app);
    await until(async () => (await app.jobs.get(running))!.state === 'interrupted');
    await until(async () => (await app.jobs.get(queued))!.state === 'done');
  });
});

describe('paths', () => {
  it('refuses anything outside the root, including through a symlink', async () => {
    const app = await makeApp();
    expect(app.safePath('KV7015/course-map.md')).toBe(join(root, 'KV7015/course-map.md'));
    expect(() => app.safePath('../outside')).toThrow(Forbidden);
    expect(() => app.safePath('%2e%2e/outside')).toThrow(Forbidden);
    expect(() => app.safePath('')).toThrow(Forbidden);
    expect(() => app.safePath('a\0b')).toThrow(Forbidden);
    symlinkSync(base, join(root, 'escape'));
    expect(() => app.safePath('escape/bcn.log')).toThrow(Forbidden);
    expect(() => app.safePath('escape/not-yet-there')).toThrow(Forbidden);
  });

  it('accepts topic ids and tree paths as targets, and nothing else', async () => {
    const app = await makeApp();
    expect(app.targetRel('KV7015-U01-T01')).toBe('KV7015/U01/T01');
    expect(app.targetRel('KV7015/U01')).toBe('KV7015/U01');
    expect(app.targetRel('.')).toBe('.');
    for (const bad of ['..', 'KV7015/../x', '/etc', 'KV7015/U1', 'kv7015']) expect(() => app.targetRel(bad)).toThrow();
  });
});

describe('shutdown', () => {
  it('closes promptly with a live event stream open', async () => {
    const { createServer } = await import('../src/index.js');
    const server = await createServer({ root, bcn: FAKE, dataDir, port: 0 });
    const ctrl = new AbortController();
    const res = await fetch(`${server.url}/api/events`, { signal: ctrl.signal });
    await res.body!.getReader().read();
    const t = Date.now();
    await server.close();
    expect(Date.now() - t).toBeLessThan(2000);
    ctrl.abort();
  });
});
