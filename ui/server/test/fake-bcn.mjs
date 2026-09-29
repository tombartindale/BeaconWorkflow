#!/usr/bin/env node
// A stand-in for bcn with controllable timing, for testing the job queue.
//   FAKE_BCN_MS    how long a job command runs (default 1500)
//   FAKE_BCN_LOG   a file each invocation and any SIGINT is appended to
import { appendFileSync } from 'node:fs';

const [command, target] = process.argv.slice(2);
const log = (line) => { if (process.env.FAKE_BCN_LOG) appendFileSync(process.env.FAKE_BCN_LOG, `${line}\n`); };
const envelope = (extra = {}) => JSON.stringify({ tool: command, schema: 1, target: target || '', ok: true, started: '2026-01-01T00:00:00Z',
  duration_ms: 1, results: [], artifacts: [], diagnostics: [], ...extra });

if (['status', 'doctor', 'codes', 'show', 'diagnostics', 'review', 'sync'].includes(command)) {
  const summary = { topics: 0, complete: { en: 0, zh: 0 }, blocked: 0, stale: 0, cloud: 0, cloud_share: 0, unreviewed: 0,
    diagnostics: { error: 0, warn: 0, info: 0 }, modules: {} };
  process.stdout.write(envelope(command === 'status' ? { summary } : command === 'codes' ? { codes: [] } : {}));
  process.exit(0);
}

log(`start ${command} ${target}`);
const ms = Number(process.env.FAKE_BCN_MS || 1500);
const t0 = Date.now();
const beat = setInterval(() => {
  process.stderr.write(`${JSON.stringify({ event: 'progress', topic: target, pct: Math.min(100, ((Date.now() - t0) / ms) * 100) })}\n`);
}, 100);
process.on('SIGINT', () => {
  clearInterval(beat);
  log(`sigint ${command} ${target}`);
  process.stdout.write(envelope({ ok: false, cancelled: true }));
  process.exit(130);
});
setTimeout(() => {
  clearInterval(beat);
  log(`done ${command} ${target}`);
  process.stdout.write(envelope());
  process.exit(0);
}, ms);
