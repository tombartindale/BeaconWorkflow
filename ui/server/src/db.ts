// The UI's own small Postgres database: job history and preferences, nothing else.
//
// Pipeline state never goes in here. Losing this database loses job history and
// preferences; every topic still looks exactly as done as it is. Connects via
// DATABASE_URL (a standard Postgres connection string), so the UI can run as one of
// several services in a multi-user Docker Compose stack instead of a single-user
// desktop app with a local file.
import { Pool } from 'pg';
import type { Prefs } from '@beacon/shared';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    created TEXT NOT NULL,
    started TEXT,
    finished TEXT,
    by TEXT,
    command TEXT NOT NULL,
    label TEXT NOT NULL,
    targets TEXT NOT NULL,      -- JSON list of paths relative to the root
    args TEXT NOT NULL,         -- JSON object
    state TEXT NOT NULL,        -- queued | running | done | failed | cancelled | interrupted
    exit_code INTEGER,
    duration_ms INTEGER,
    envelope TEXT,              -- JSON: the envelope(s) exactly as bcn printed them
    log TEXT,                   -- the tail of stderr, NDJSON lines as received
    -- The rest exist so any API replica can read a running job's live state, since the
    -- worker actually running it may be a different process (even a different host).
    target_index INTEGER NOT NULL DEFAULT 0,
    progress TEXT,              -- JSON: the most recent 'progress' event from bcn's stderr
    last_event TEXT,            -- when progress/log was last observed; used for stall detection
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    worker_id TEXT              -- which worker process is running this job, for diagnostics
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- One row per live worker process, refreshed on a short interval. A 'running' job whose
-- worker has no recent heartbeat was abandoned by a crashed/killed worker and is marked
-- interrupted, the same way a restarted single-process backend used to recover its own
-- crashed jobs.
CREATE TABLE IF NOT EXISTS worker_heartbeats (worker_id TEXT PRIMARY KEY, last_seen TEXT NOT NULL);
`;

export const DEFAULT_PREFS: Prefs = {
  operator: '',
  jobs: 1,                 // passed through to bcn --jobs
  parallel_jobs: 2,        // queue jobs running at once (never two on the same topic)
  theme: '',               // empty: let bcn resolve it
  poll_seconds: 15,
  module_columns: { en: true, zh: true },
  topic_layout: 'side',    // side | stacked
};

export interface JobRow {
  id: number; created: string; started: string | null; finished: string | null; by: string | null;
  command: string; label: string; targets: string; args: string; state: string;
  exit_code: number | null; duration_ms: number | null; envelope: string | null; log: string | null;
  target_index: number; progress: string | null; last_event: string | null;
  cancel_requested: boolean; worker_id: string | null;
}

export class DB {
  readonly pool: Pool;

  constructor(connectionString: string) {
    this.pool = new Pool({ connectionString });
  }

  /** Creates the schema if missing. Must be awaited once before the DB is used. */
  async init(): Promise<void> {
    await this.pool.query(SCHEMA);
  }

  async prefs(): Promise<Prefs> {
    const out: Record<string, unknown> = { ...DEFAULT_PREFS };
    const res = await this.pool.query<{ key: string; value: string }>('SELECT key, value FROM prefs');
    for (const row of res.rows) {
      try { out[row.key] = JSON.parse(row.value); } catch { /* ignore a damaged value */ }
    }
    return out as unknown as Prefs;
  }

  async setPrefs(values: Record<string, unknown>): Promise<Prefs> {
    for (const [k, v] of Object.entries(values)) {
      if (k in DEFAULT_PREFS) {
        await this.pool.query('INSERT INTO prefs(key, value) VALUES($1, $2) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
          [k, JSON.stringify(v)]);
      }
    }
    return this.prefs();
  }

  async close(): Promise<void> { await this.pool.end(); }
}
