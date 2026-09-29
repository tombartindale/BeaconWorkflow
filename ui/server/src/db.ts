// The UI's own small SQLite file: job history and preferences, nothing else.
//
// Pipeline state never goes in here. Deleting this file loses job history and
// preferences; every topic still looks exactly as done as it is. The schema is the
// Python backend's, so an existing file carries over.
import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Prefs } from '@beacon/shared';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    log TEXT                    -- the tail of stderr, NDJSON lines as received
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
}

export class DB {
  readonly conn: Database.Database;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.conn = new Database(path);
    this.conn.pragma('journal_mode = WAL');
    this.conn.exec(SCHEMA);
  }

  prefs(): Prefs {
    const out: Record<string, unknown> = { ...DEFAULT_PREFS };
    for (const row of this.conn.prepare('SELECT key, value FROM prefs').all() as Array<{ key: string; value: string }>) {
      try { out[row.key] = JSON.parse(row.value); } catch { /* ignore a damaged value */ }
    }
    return out as unknown as Prefs;
  }

  setPrefs(values: Record<string, unknown>): Prefs {
    const put = this.conn.prepare('INSERT INTO prefs(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value');
    for (const [k, v] of Object.entries(values)) {
      if (k in DEFAULT_PREFS) put.run(k, JSON.stringify(v));
    }
    return this.prefs();
  }

  close(): void { this.conn.close(); }
}
