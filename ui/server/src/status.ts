// Programme state, which is always bcn status's and never the UI's own.
//
// Polled on a timer, refreshed immediately after any job finishes, and refreshed when the
// tree changes on disk (files arrive by sync, not only through the UI). Held in memory
// with a short TTL so moving between views does not rescan.
import type { Queried, StatusEnvelope as BcnStatusEnvelope } from '@beacon/shared';
import type { Bcn } from './bcn.js';
import type { Bus } from './bus.js';
import { BcnError } from './errors.js';
import { sha1 } from './util.js';

export const TTL_MS = 3000;
type StatusEnv = Queried<BcnStatusEnvelope>;

export class StatusCache {
  private env: StatusEnv | null = null;
  private at = 0;
  private refreshing: Promise<StatusEnv | null> | null = null;
  private fingerprint = '';
  private timer: NodeJS.Timeout | null = null;
  version = 0;
  error: string | null = null;

  constructor(private bcn: Bcn, private bus: Bus, private pollSeconds: () => Promise<number>) {}

  start(): void {
    const tick = () => {
      void this.pollSeconds().then((seconds) => {
        this.timer = setTimeout(() => { void this.refresh('poll').finally(tick); }, Math.max(3, seconds) * 1000);
      });
    };
    tick();
  }

  stop(): void { if (this.timer) clearTimeout(this.timer); }

  async get(maxAgeMs = TTL_MS): Promise<StatusEnv | null> {
    if (this.env && Date.now() - this.at < maxAgeMs) return this.env;
    return this.refresh('read');
  }

  /** Another refresh already running is waited for rather than scanning twice. */
  refresh(reason: string): Promise<StatusEnv | null> {
    if (this.refreshing) return this.refreshing.then(() => this.env);
    this.refreshing = this.run(reason).finally(() => { this.refreshing = null; });
    return this.refreshing;
  }

  private async run(reason: string): Promise<StatusEnv | null> {
    let env: StatusEnv;
    try {
      env = await this.bcn.query<BcnStatusEnvelope>('status', [this.bcn.root]);
      this.error = null;
    } catch (e) {
      if (!(e instanceof BcnError)) throw e;
      this.error = e.message;
      this.bus.publish({ type: 'status-error', error: this.error });
      return this.env;
    }
    const fp = fingerprint(env);
    const changed = this.env === null || fp !== this.fingerprint;
    this.env = env;
    this.at = Date.now();
    this.fingerprint = fp;
    if (changed) {
      this.version += 1;
      this.bus.publish({ type: 'status', version: this.version, reason, summary: env.summary });
    }
    return env;
  }
}

// Artefact times are included so that any new or rebuilt file (a recording script, a draft)
// refreshes the views showing it, even when no stage changes.
function fingerprint(env: StatusEnv): string {
  const slim = (env.results || []).map((r) => [r.topic, r.en, r.zh, r.hydration, r.unreviewed_mistranscriptions,
    (r.artifacts || []).map((a) => [a.key, a.mtime])]);
  return sha1(JSON.stringify([slim, env.diagnostics]));
}
