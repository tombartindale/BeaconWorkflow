// The backend's state: the programme root, the database, bcn, status, jobs and the watcher.
// Everything the HTTP routes need, and nothing to do with HTTP.
import { existsSync, readdirSync, realpathSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, join, resolve, sep } from 'node:path';
import type { CodeInfo, DoctorEnvelope, JobArgs, JobSummary, Queried } from '@beacon/shared';
import { Auth, authOptionsFromEnv } from './auth.js';
import { Bcn } from './bcn.js';
import { Bus, type EventBus } from './bus.js';
import { DB } from './db.js';
import { BadRequest, BcnError, Forbidden, StartupError } from './errors.js';
import { JobQueue } from './jobs.js';
import { StatusCache } from './status.js';
import { sha1 } from './util.js';
import { Watcher } from './watcher.js';

export const TOPIC_ID_RE = /^([A-Z]{2}\d{4})-(U\d{2})-(T\d{2})$/;
const TARGET_RE = /^(\.|[A-Z]{2}\d{4}(\/U\d{2}(\/T\d{2})?)?)$/;

/** Outside the programme root on purpose: UI state must never sync to SharePoint or be mistaken for content. */
export function defaultDataDir(root: string): string {
  let real = resolve(root);
  try { real = realpathSync(real); } catch { /* checkRoot will say what is wrong */ }
  return join(homedir(), 'Library', 'Application Support', 'BeaconUI', sha1(real).slice(0, 12));
}

/** Fail clearly rather than present an empty dashboard that looks like nothing is done. */
export function checkRoot(root: string): string[] {
  if (!existsSync(root) || !statSync(root).isDirectory()) throw new StartupError(`The programme root ${root} does not exist.`);
  if (!existsSync(join(root, 'programme.toml'))) throw new StartupError(`${root} has no programme.toml; it is not a programme root.`);
  const modules = readdirSync(root, { withFileTypes: true }).filter((e) => e.isDirectory() && /^[A-Z]{2}\d{4}$/.test(e.name));
  if (!modules.length) throw new StartupError(`${root} contains no module folders (named like KV7015).`);
  const probe = join(root, `.beacon-ui-write-test-${process.pid}`);
  try {
    writeFileSync(probe, 'x');
    unlinkSync(probe);
  } catch (e) {
    throw new StartupError(`${root} is not writable: ${(e as Error).message}`);
  }
  const warnings: string[] = [];
  const real = realpathSync(root);
  if (real.includes('CloudStorage') || real.includes('OneDrive')) {
    warnings.push('The programme root is inside a synced folder. Files may be cloud-only placeholders; '
      + 'keeping the root on local disk and copying to SharePoint deliberately avoids that.');
  }
  return warnings;
}

export function jobLabel(command: string, targets: string[], args: JobArgs): string {
  let what = command;
  if (args.lang === 'zh') what += ' (zh)';
  if (args.force) what += ' --force';
  if (command === 'cues' && args.set) what = `cues: set slide ${args.set}`;
  if (command === 'review') what = `review: ${args.accept ? 'accept' : args.correct ? 'correct' : 'clear'}`;
  if (command === 'ack') what = args.clear ? 'withdraw acknowledgement' : `acknowledge ${String(args.fingerprint ?? '').split('|')[0]}`;
  if (command === 'sync') what = `${args.pull ? 'pull from OneDrive' : 'push to OneDrive'}${args.prefer ? ' (keep chosen version)' : ''}`;
  if (command === 'translation') what = args.export ? 'translation export' : 'translation import';
  const scope = targets.length === 1 ? targets[0] : `${targets.length} targets`;
  return `${what} · ${scope === '.' ? 'programme' : scope}`;
}

export interface AppOptions { root: string; bcn: string[]; dataDir: string }

export class App {
  readonly root: string;
  readonly dataDir: string;
  readonly warnings: string[];
  readonly db: DB;
  readonly bus: EventBus;
  readonly bcn: Bcn;
  readonly status: StatusCache;
  readonly jobs: JobQueue;
  readonly watcher: Watcher;
  readonly auth: Auth;
  doctor: Queried<DoctorEnvelope> | null = null;
  codes: CodeInfo[] = [];
  private queryCache = new Map<string, { at: number; env: Record<string, unknown> }>();

  private constructor(opts: AppOptions, db: DB, bus: EventBus) {
    this.warnings = checkRoot(resolve(opts.root));
    this.root = realpathSync(resolve(opts.root));
    this.dataDir = opts.dataDir;
    this.db = db;
    this.bus = bus;
    this.bcn = new Bcn(opts.bcn, this.root);
    this.status = new StatusCache(this.bcn, this.bus, async () => Number((await this.db.prefs()).poll_seconds) || 15);
    this.jobs = new JobQueue(db, this.bus);
    this.watcher = new Watcher(this.root, (reason) => { void this.status.refresh(reason); });
    this.auth = new Auth(db, authOptionsFromEnv());
    // The worker running a job publishes its completion on the bus (Redis, if the worker is
    // a separate process) rather than calling back into this process directly.
    this.bus.subscribe((ev) => {
      if (ev.type === 'job' && ['done', 'failed', 'cancelled'].includes(ev.job.state)) this.jobFinished(ev.job);
    });
  }

  static async create(opts: AppOptions, bus: EventBus = new Bus()): Promise<App> {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new StartupError('DATABASE_URL is required (a Postgres connection string).');
    const db = new DB(connectionString);
    await db.init();
    await Auth.init(db);
    return new App(opts, db, bus);
  }

  start(): void {
    this.status.start();
    this.watcher.start();
    void this.warm();
  }

  async stop(): Promise<void> {
    this.status.stop();
    this.watcher.stop();
    await this.db.close();
  }

  private async warm(): Promise<void> {
    try {
      this.doctor = await this.bcn.query<DoctorEnvelope>('doctor', [this.root]);
      this.codes = (await this.bcn.query<{ codes?: CodeInfo[] }>('codes')).codes || [];
    } catch (e) {
      if (!(e instanceof BcnError)) throw e;
      this.warnings.push(`bcn could not run: ${e.message}`);
    }
    const env = await this.status.refresh('startup');
    const share = env?.summary?.cloud_share || 0;
    if (share && share >= 0.1) {
      this.warnings.push(`${Math.round(share * 100)}% of topics have cloud-only files. A pipeline that processes a placeholder `
        + 'produces convincing rubbish: make the folders available offline in Finder '
        + '(Always Keep on This Device) before running jobs.');
      this.bus.publish({ type: 'warnings', warnings: this.warnings });
    }
  }

  private jobFinished(_job: JobSummary): void {
    this.queryCache.clear();
    void this.status.refresh(`job ${_job.id} finished`);
  }

  clearQueryCache(): void { this.queryCache.clear(); }

  /** A bcn query, reused for `ttlMs` unless status has changed since. */
  async cachedQuery<T>(key: string, ttlMs: number, command: string, args: string[]): Promise<T> {
    const hit = this.queryCache.get(key);
    if (hit && Date.now() - hit.at < ttlMs && hit.env._status_version === this.status.version) return hit.env as T;
    const env = await this.bcn.query<Record<string, unknown>>(command, args);
    env._status_version = this.status.version;
    this.queryCache.set(key, { at: Date.now(), env });
    return env as T;
  }

  /** A path beneath the root, symlinks resolved. Anything else is refused. */
  safePath(rel: string): string {
    let decoded: string;
    try { decoded = decodeURIComponent(rel); } catch { decoded = rel; }
    decoded = decoded.replace(/^\/+/, '');
    if (!decoded || decoded.includes('\0')) throw new Forbidden();
    const p = realpathOrResolve(join(this.root, decoded));
    if (p !== this.root && !p.startsWith(this.root + sep)) throw new Forbidden();
    return p;
  }

  /** A topic id or a programme/module/unit/topic path, as a path relative to the root. */
  targetRel(value: string): string {
    const m = TOPIC_ID_RE.exec(value);
    const rel = m ? m.slice(1).join('/') : value;
    if (!TARGET_RE.test(rel)) throw new BadRequest(`'${rel}' is not a programme, module, unit or topic.`);
    return rel;
  }

  /** Where a target lives on disk. */
  targetPath(rel: string): string { return rel === '.' ? this.root : join(this.root, rel); }

  async operator(): Promise<string> {
    return (await this.db.prefs()).operator || process.env.USER || '';
  }
}

/** Like Python's Path.resolve(): symlinks resolved as far as the path exists. */
function realpathOrResolve(p: string): string {
  const abs = resolve(p);
  try { return realpathSync(abs); } catch { /* does not exist (yet) */ }
  const parts: string[] = [];
  let dir = abs;
  for (;;) {
    const parent = resolve(dir, '..');
    parts.unshift(basename(dir));
    if (parent === dir) return abs;
    dir = parent;
    try { return join(realpathSync(dir), ...parts); } catch { /* keep climbing */ }
  }
}
