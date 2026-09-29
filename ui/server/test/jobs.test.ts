// The job queue and path safety, against a stand-in bcn whose timing the tests control.
import { cpSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from '../src/app.js';
import { DB } from '../src/db.js';
import { Forbidden } from '../src/errors.js';
import { overlaps } from '../src/jobs.js';
import { sleep } from '../src/util.js';
import { REPO } from './harness.js';

const FAKE = [process.execPath, join(import.meta.dirname, 'fake-bcn.mjs')];

let base: string;
let root: string;
let dataDir: string;
let logFile: string;
const apps: App[] = [];

function makeApp(): App {
  const app = new App({ root, bcn: FAKE, dataDir });
  apps.push(app);
  return app;
}
const events = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n') : []);
async function until(check: () => boolean, ms = 10_000) {
  const t = Date.now();
  while (!check()) {
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

afterEach(() => {
  for (const app of apps.splice(0)) { try { app.stop(); } catch { /* already stopped */ } }
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
    const app = makeApp();
    app.db.setPrefs({ parallel_jobs: 2 });
    app.jobs.start();
    const a = app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = app.jobs.submit('validate', ['KV7015/U01/T01'], {}, 'b', 't');
    const c = app.jobs.submit('validate', ['KV7015/U02'], {}, 'c', 't');
    await until(() => events().length >= 2);
    await sleep(200);
    expect(app.jobs.get(a.id)!.state).toBe('running');
    expect(app.jobs.get(b.id)!.state).toBe('queued');      // waits for a: same unit
    expect(app.jobs.get(c.id)!.state).toBe('running');     // different unit: alongside a
    await until(() => app.jobs.get(b.id)!.state === 'done');
    const order = events().filter((e) => e.startsWith('start')).map((e) => e.split(' ')[2]);
    expect(order.indexOf(join(root, 'KV7015/U01/T01'))).toBeGreaterThan(order.indexOf(join(root, 'KV7015/U01')));
    expect(app.jobs.get(a.id)).toMatchObject({ state: 'done', exit_code: 0, ok: true });
  });

  it('respects the parallel limit', async () => {
    const app = makeApp();
    app.db.setPrefs({ parallel_jobs: 1 });
    app.jobs.start();
    const a = app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = app.jobs.submit('validate', ['KV7015/U02'], {}, 'b', 't');
    await until(() => app.jobs.get(a.id)!.state === 'running');
    await sleep(300);
    expect(app.jobs.get(b.id)!.state).toBe('queued');
    await until(() => app.jobs.get(b.id)!.state === 'done');
  });

  it('cancelling a running job sends SIGINT and lets bcn finish its envelope', async () => {
    process.env.FAKE_BCN_MS = '20000';
    const app = makeApp();
    app.jobs.start();
    const j = app.jobs.submit('render', ['KV7015/U01/T01'], {}, 'r', 't');
    await until(() => events().some((e) => e.startsWith('start')));
    app.jobs.cancel(j.id);
    await until(() => app.jobs.get(j.id)!.state === 'cancelled');
    expect(events().some((e) => e.startsWith('sigint render'))).toBe(true);
    const detail = app.jobs.get(j.id)!;
    expect(detail.exit_code).toBe(130);
    expect(detail.envelopes[0]).toMatchObject({ cancelled: true });
  });

  it('cancelling a queued job never runs it', async () => {
    const app = makeApp();
    app.db.setPrefs({ parallel_jobs: 1 });
    app.jobs.start();
    app.jobs.submit('validate', ['KV7015/U01'], {}, 'a', 't');
    const b = app.jobs.submit('validate', ['KV7015/U02'], {}, 'b', 't');
    expect(app.jobs.cancel(b.id)!.state).toBe('cancelled');
    await sleep(2500);
    expect(events().some((e) => e.includes('KV7015/U02'))).toBe(false);
  });

  it('streams progress to the bus and records the log', async () => {
    const app = makeApp();
    const seen: string[] = [];
    const progress: unknown[] = [];
    app.bus.subscribe((ev) => {
      seen.push(ev.type);
      if (ev.type === 'job-event' && ev.event.event === 'progress') progress.push(ev.event);
    });
    app.jobs.start();
    const j = app.jobs.submit('validate', ['KV7015/U01/T01'], { lang: 'en' }, 'v', 't');
    await until(() => app.jobs.get(j.id)!.state === 'done');
    expect(seen).toContain('job-event');
    const d = app.jobs.get(j.id)!;
    expect(d.log[0]).toBe(`{"event": "log", "level": "info", "message": "$ bcn validate ${join(root, 'KV7015/U01/T01')} --lang en --jobs 1"}`);
    // Progress is live only: a finished job is read back from the database without it, as before.
    expect(progress.length).toBeGreaterThan(3);
    expect(d.log.some((l) => l.includes('"progress"'))).toBe(true);
  });

  it('marks jobs running at shutdown as interrupted, and picks up queued ones', async () => {
    const db = new DB(join(dataDir, 'ui.sqlite'));
    const insert = db.conn.prepare("INSERT INTO jobs(created, by, command, label, targets, args, state) VALUES('x', 't', 'validate', 'l', ?, '{}', ?)");
    const running = Number(insert.run('["KV7015/U01"]', 'running').lastInsertRowid);
    const queued = Number(insert.run('["KV7015/U02"]', 'queued').lastInsertRowid);
    db.close();
    const app = makeApp();
    expect(app.jobs.get(running)!.state).toBe('interrupted');
    app.jobs.start();
    await until(() => app.jobs.get(queued)!.state === 'done');
  });
});

describe('paths', () => {
  it('refuses anything outside the root, including through a symlink', () => {
    const app = makeApp();
    expect(app.safePath('KV7015/course-map.md')).toBe(join(root, 'KV7015/course-map.md'));
    expect(() => app.safePath('../outside')).toThrow(Forbidden);
    expect(() => app.safePath('%2e%2e/outside')).toThrow(Forbidden);
    expect(() => app.safePath('')).toThrow(Forbidden);
    expect(() => app.safePath('a\0b')).toThrow(Forbidden);
    symlinkSync(base, join(root, 'escape'));
    expect(() => app.safePath('escape/bcn.log')).toThrow(Forbidden);
    expect(() => app.safePath('escape/not-yet-there')).toThrow(Forbidden);
  });

  it('accepts topic ids and tree paths as targets, and nothing else', () => {
    const app = makeApp();
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
