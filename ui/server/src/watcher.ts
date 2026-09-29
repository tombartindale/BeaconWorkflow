// Polls the tree's source files and step results and refreshes status once changes settle.
//
// Stats only the names the pipeline cares about, the same allowlist bcn uses, so it stays
// cheap across a whole programme and never opens a file. Polling rather than fs.watch on
// purpose: file events from OneDrive and other cloud folders are unreliable (spec §2.3).
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { sha1 } from './util.js';

export const QUIET_MS = 2000;  // a changing tree must settle this long before it is read: sync writes arrive in pieces
const MODULE_RE = /^[A-Z]{2}\d{4}$/;
const UNIT_RE = /^U\d{2}$/;
const TOPIC_RE = /^T\d{2}$/;

async function dirs(d: string, rx: RegExp): Promise<string[]> {
  try {
    return (await readdir(d, { withFileTypes: true })).filter((e) => e.isDirectory() && rx.test(e.name)).map((e) => e.name).sort();
  } catch { return []; }
}

async function files(d: string): Promise<string[]> {
  try {
    return (await readdir(d, { withFileTypes: true })).filter((e) => e.isFile() && !e.name.startsWith('.')).map((e) => e.name).sort();
  } catch { return []; }
}

export async function signature(root: string): Promise<string> {
  const paths: string[] = [join(root, 'programme.toml')];
  for (const m of await dirs(root, MODULE_RE)) {
    const md = join(root, m);
    for (const name of await files(md)) paths.push(join(md, name));
    paths.push(join(md, 'build', 'validate.json'));
    for (const u of await dirs(md, UNIT_RE)) {
      const ud = join(md, u);
      paths.push(join(ud, 'activity.md'));
      for (const t of await dirs(ud, TOPIC_RE)) {
        const td = join(ud, t);
        for (const name of await files(td)) paths.push(join(td, name));
        for (const [sub, suffix] of [['edit', null], ['assets', null], ['build', '.json'], ['out', '.json']] as const) {
          const sd = join(td, sub);
          for (const name of await files(sd)) if (!suffix || name.endsWith(suffix)) paths.push(join(sd, name));
        }
      }
    }
  }
  const lines = await Promise.all(paths.map(async (p) => {
    try {
      const st = await stat(p, { bigint: true });
      return `${p}|${st.mtimeNs}|${st.size}\n`;
    } catch { return ''; }
  }));
  return sha1(lines.join(''));
}

export class Watcher {
  private timer: NodeJS.Timeout | null = null;
  private stopped = false;

  constructor(private root: string, private onChange: (reason: string) => void, private intervalMs = 2000) {}

  start(): void {
    let last: string | null = null;
    let pendingSince: number | null = null;
    const loop = async () => {
      if (this.stopped) return;
      try {
        const sig = await signature(this.root);
        if (last === null) last = sig;
        else if (sig !== last) {
          last = sig;
          pendingSince = Date.now();  // still changing: wait for it to settle
        } else if (pendingSince && Date.now() - pendingSince >= QUIET_MS) {
          pendingSince = null;
          this.onChange('tree changed');
        }
      } catch { /* try again next time */ }
      if (!this.stopped) this.timer = setTimeout(loop, this.intervalMs);
    };
    void loop();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }
}
