import { createHash } from 'node:crypto';

/** UTC to the second, as the job records have always stored it. */
export function now(date = new Date()): string {
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Local time with microseconds, for naming pasted files: 20260929-165012-123456. */
export function stamp(date = new Date()): string {
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  const micro = p(date.getMilliseconds(), 3) + String(Math.floor(Math.random() * 1000)).padStart(3, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}-${micro}`;
}

export function sha1(text: string): string { return createHash('sha1').update(text).digest('hex'); }
export function sha256(data: Buffer | string): string { return createHash('sha256').update(data).digest('hex'); }

/**
 * JSON as Python's json.dumps writes it (", " and ": ", non-ASCII escaped). Log lines the
 * backend adds itself sit beside bcn's own NDJSON, so they are written the same way.
 */
export function pyJson(value: unknown): string {
  const ascii = (s: string) => s.replace(/[\u007f-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  const walk = (v: unknown): string => {
    if (Array.isArray(v)) return `[${v.map(walk).join(', ')}]`;
    if (v && typeof v === 'object') {
      return `{${Object.entries(v).map(([k, x]) => `${ascii(JSON.stringify(k))}: ${walk(x)}`).join(', ')}}`;
    }
    return ascii(JSON.stringify(v ?? null));
  };
  return walk(value);
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
