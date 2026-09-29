// Running bcn. The backend is the only thing that does.
//
// Read-only queries (status, show, diagnostics, review listing, codes, doctor) run
// directly here. Anything that changes files goes through the job queue.
import { execFile } from 'node:child_process';
import { accessSync, constants } from 'node:fs';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BcnError } from './errors.js';

export const QUERY_TIMEOUT_MS = 180_000;
const MAX_ENVELOPE_BYTES = 256 * 1024 * 1024;

function executable(p: string): boolean {
  try { accessSync(p, constants.X_OK); return true; } catch { return false; }
}

/** The command prefix that runs bcn: --bcn, then $BCN, then the repo's tooling venv, then PATH. */
export function locateBcn(explicit?: string | null): string[] {
  if (explicit) return [explicit];
  if (process.env.BCN) return [process.env.BCN];
  const repo = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
  const sibling = join(repo, 'tooling', '.venv', 'bin', 'bcn');
  if (executable(sibling)) return [sibling];
  for (const dir of (process.env.PATH || '').split(delimiter)) {
    if (dir && executable(join(dir, 'bcn'))) return [join(dir, 'bcn')];
  }
  return ['python3', '-m', 'bcn'];
}

export class Bcn {
  constructor(readonly prefix: string[], readonly root: string) {}

  argv(command: string, ...args: string[]): string[] {
    return [...this.prefix, command, ...args, '--quiet'];
  }

  /** Run a read-only command and return its envelope, whatever its exit code. */
  query<T = Record<string, unknown>>(command: string, args: string[] = [], timeoutMs = QUERY_TIMEOUT_MS): Promise<T & { exit_code: number }> {
    const [file, ...rest] = this.argv(command, ...args);
    return new Promise((resolve, reject) => {
      const child = execFile(file, rest, { cwd: this.root, timeout: timeoutMs, maxBuffer: MAX_ENVELOPE_BYTES, encoding: 'utf8' },
        (err, stdout, stderr) => {
          // A non-zero exit still carries an envelope; only a failure to run at all is an error.
          const e = err as (NodeJS.ErrnoException & { killed?: boolean; code?: number | string }) | null;
          if (e && (typeof e.code === 'string' || e.killed)) {
            reject(new BcnError(`bcn ${command} could not run: ${e.killed ? 'timed out' : e.message}`));
            return;
          }
          let env: T & { exit_code: number };
          try {
            env = JSON.parse(stdout);
          } catch {
            reject(new BcnError(`bcn ${command} printed no envelope (exit ${child.exitCode}): ${String(stderr).slice(-500)}`));
            return;
          }
          env.exit_code = child.exitCode ?? (typeof e?.code === 'number' ? e.code : 0);
          resolve(env);
        });
      child.stdin?.end();
    });
  }
}
