// Starts the backend against a private copy of example/. The contract fixtures were first
// recorded from the Python backend this one replaced, and now pin its behaviour.
//
// TODO(postgres): the backend's job history and preferences now live in Postgres
// (see src/db.ts), so this needs a real or containerized Postgres reachable at
// DATABASE_URL to run. Point DATABASE_URL at a throwaway database before running these
// tests (e.g. `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16` and
// `DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres npm test -w server`).
// Each backend gets its own schema-per-run isn't set up yet; that's left for whoever
// wires this into CI/Docker Compose, since setting up a full containerized Postgres for
// tests is out of scope for the SQLite-to-Postgres conversion itself.
import { spawn, type ChildProcess } from 'node:child_process';
import { cpSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
export interface Backend {
  url: string;
  port: number;
  root: string;
  dataDir: string;
  logs: () => string;
  stop: () => Promise<void>;
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.listen(0, '127.0.0.1', () => {
      const addr = s.address();
      s.close(() => (addr && typeof addr === 'object' ? resolve(addr.port) : reject(new Error('no port'))));
    });
  });
}

export async function startBackend(): Promise<Backend> {
  const base = realpathSync(mkdtempSync(join(tmpdir(), 'beacon-contract-')));
  const root = join(base, 'root');
  const dataDir = join(base, 'data');
  // Timestamps are kept so staleness comes out the same in every copy.
  cpSync(join(REPO, 'example'), root, { recursive: true, preserveTimestamps: true });
  const port = await freePort();
  // --test-disable-auth: the contract suite exercises the API directly, without a browser
  // to hold a session cookie from the magic-link login flow (see server.ts's auth gate).
  const args = ['--root', root, '--port', String(port), '--data-dir', dataDir, '--test-disable-auth'];
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL must point at a Postgres instance to run these tests; see the TODO at the top of this file.');
  const child: ChildProcess = spawn(process.execPath, ['--import', 'tsx', join(REPO, 'ui', 'server', 'src', 'cli.ts'), ...args],
    { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, BCN: join(REPO, 'tooling', '.venv', 'bin', 'bcn') } });
  let log = '';
  child.stdout?.on('data', (b) => { log += b; });
  child.stderr?.on('data', (b) => { log += b; });
  const url = `http://127.0.0.1:${port}`;
  const stop = async () => {
    if (child.exitCode === null) {
      child.kill('SIGTERM');
      await new Promise((r) => child.once('exit', r));
    }
    rmSync(base, { recursive: true, force: true });
  };
  // Ready when status can be read and the startup queries (doctor, codes) have landed.
  const deadline = Date.now() + 120_000;
  for (;;) {
    if (child.exitCode !== null) throw new Error(`backend exited (${child.exitCode}):\n${log}`);
    try {
      const boot = await (await fetch(`${url}/api/boot`)).json() as { codes: unknown[]; doctor: unknown };
      if (boot.doctor && boot.codes.length && (await fetch(`${url}/api/status`)).ok) break;
    } catch { /* not listening yet */ }
    if (Date.now() > deadline) { await stop(); throw new Error(`backend did not start:\n${log}`); }
    await new Promise((r) => setTimeout(r, 300));
  }
  return { url, port, root, dataDir, logs: () => log, stop };
}

// -- normalising responses so two backends (and two runs) can be compared -------------------------
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/;
const VOLATILE_NUMBERS = new Set(['duration_ms', 'elapsed_ms', 'eta_ms', 'last_event_age_ms', 'version', 'status_version', '_status_version']);

export function normalise(value: unknown, b: Backend): unknown {
  const text = (s: string) => {
    let out = s.split(b.root).join('<ROOT>').split(b.dataDir).join('<DATA>');
    if (ISO.test(out)) out = '<TIME>';
    // Intake and edit pastes are named by the clock.
    out = out.replace(/\d{8}-\d{6}-\d{6}\.md/g, '<STAMP>.md');
    return out;
  };
  const walk = (v: unknown, key?: string): unknown => {
    if (typeof v === 'string') return text(v);
    if (typeof v === 'number' && key && VOLATILE_NUMBERS.has(key)) return '<N>';
    if (Array.isArray(v)) return v.map((x) => walk(x));
    if (v && typeof v === 'object') {
      const out: Record<string, unknown> = {};
      for (const k of Object.keys(v).sort()) out[k] = walk((v as Record<string, unknown>)[k], k);
      return out;
    }
    return v;
  };
  return walk(value);
}
