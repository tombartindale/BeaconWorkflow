// The HTTP API is the contract (spec §2.3). Every case here was recorded from the Python
// backend (npm run record -w server) and the Node backend must give the same answers,
// apart from times, durations and where the temporary root lives.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { normalise, startBackend, type Backend } from './harness.js';

const FIXTURES = join(import.meta.dirname, 'contract');
const RECORD = process.env.RECORD === '1';
const TOPIC = 'KV7015-U01-T01';

let b: Backend;
beforeAll(async () => { b = await startBackend(); });
afterAll(async () => { await b?.stop(); });

interface Reply { status: number; type: string; body: unknown; headers?: Record<string, string> }

async function call(path: string, init: RequestInit = {}): Promise<Reply> {
  const r = await fetch(b.url + path, init);
  const type = (r.headers.get('content-type') || '').split(';')[0];
  const text = await r.text();
  let body: unknown = text;
  if (type === 'application/json') body = JSON.parse(text);
  return { status: r.status, type, body };
}
// The Python backend does not read the body of a request it rejects, or of one whose route
// ignores it, which leaves the body on a kept-alive connection. Closing avoids that.
const post = (path: string, body: unknown, method = 'POST') =>
  call(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', Connection: 'close' } });

// fetch() will not send a foreign Host header, so this one goes out by hand.
function rawGet(path: string, headers: Record<string, string>): Promise<Reply> {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ host: '127.0.0.1', port: b.port, path, headers }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve({ status: res.statusCode || 0, type: (res.headers['content-type'] || '').split(';')[0],
        body: data.startsWith('{') ? JSON.parse(data) : data }));
    });
    req.on('error', reject);
    req.end();
  });
}

async function waitForJob(id: number) {
  for (let i = 0; i < 600; i++) {
    const r = await call(`/api/jobs/${id}`);
    const job = r.body as { state: string };
    if (!['queued', 'running', 'stalled'].includes(job.state)) return r;
    await new Promise((res) => setTimeout(res, 250));
  }
  throw new Error(`job ${id} did not finish`);
}

// A finished job, reduced to what is deterministic: its record, its envelopes, and the command it ran.
function jobShape(r: Reply) {
  const j = r.body as Record<string, unknown> & { log: string[]; progress: unknown };
  return { ...r, body: { ...j, progress: j.progress ? 'present' : null, log: j.log.slice(0, 1) } };
}

function check(name: string, reply: Reply) {
  const got = normalise(reply, b);
  const file = join(FIXTURES, `${name}.json`);
  if (RECORD) {
    writeFileSync(file, JSON.stringify(got, null, 1) + '\n');
    return;
  }
  if (!existsSync(file)) throw new Error(`no fixture ${name}.json: record it from the Python backend first`);
  expect(got).toEqual(JSON.parse(readFileSync(file, 'utf8')));
}

describe(`API contract (${process.env.BEACON_BACKEND || 'node'} backend)`, () => {
  describe('reads', () => {
    it('boot', async () => {
      // Tool versions and paths belong to the machine, not the contract: keep which tools and whether they pass.
      const r = await call('/api/boot');
      const body = r.body as { doctor: { results: Array<{ topic: string; ok: boolean }> } };
      check('boot', { ...r, body: { ...body, doctor: { ...body.doctor, results: body.doctor.results.map(({ topic, ok }) => ({ topic, ok })) } } });
    });
    it('status', async () => check('status', await call('/api/status')));
    it('topic', async () => check('topic', await call(`/api/topic/${TOPIC}`)));
    it('topic verify', async () => check('topic-verify', await call(`/api/topic/${TOPIC}?verify=1`)));
    it('topic source en', async () => check('topic-source-en', await call(`/api/topic/${TOPIC}/source`)));
    it('topic source zh (missing)', async () => check('topic-source-zh', await call(`/api/topic/${TOPIC}/source?lang=zh`)));
    it('topic source, bad lang', async () => check('topic-source-bad-lang', await call(`/api/topic/${TOPIC}/source?lang=fr`)));
    it('topic check', async () => {
      const src = (await call(`/api/topic/${TOPIC}/source`)).body as { text: string };
      check('topic-check', await post(`/api/topic/${TOPIC}/check`, { text: `${src.text}\n---\n\n# Extra slide\n`, lang: 'en' }));
    });
    it('diagnostics, programme', async () => check('diagnostics-programme', await call('/api/diagnostics?path=.')));
    it('diagnostics, module', async () => check('diagnostics-module', await call('/api/diagnostics?path=KV7015')));
    it('diagnostics, topic id', async () => check('diagnostics-topic', await call(`/api/diagnostics?path=${TOPIC}`)));
    it('diagnostics, bad path', async () => check('diagnostics-bad-path', await call('/api/diagnostics?path=../etc')));
    it('review', async () => check('review', await call('/api/review?path=KV7015')));
    it('sync', async () => check('sync', await call('/api/sync')));
    it('jobs, none yet', async () => check('jobs-empty', await call('/api/jobs')));
    it('translation list', async () => check('translation', await call('/api/translation')));
    it('prefs', async () => check('prefs', await call('/api/prefs')));
  });

  describe('files', () => {
    it('text file', async () => check('files-toml', await call('/files/programme.toml')));
    it('view as text', async () => check('files-view', await call('/files/KV7015/course-map.md?view=1')));
    it('srt as vtt', async () => check('files-vtt', await call('/files/KV7015/U01/T01/edit/master.srt?format=vtt')));
    it('byte range', async () => {
      const r = await fetch(`${b.url}/files/programme.toml`, { headers: { Range: 'bytes=0-9' } });
      check('files-range', { status: r.status, type: '', body: { range: r.headers.get('content-range'), length: r.headers.get('content-length'), text: await r.text() } });
    });
    it('suffix range', async () => {
      const r = await fetch(`${b.url}/files/programme.toml`, { headers: { Range: 'bytes=-5' } });
      check('files-range-suffix', { status: r.status, type: '', body: { range: r.headers.get('content-range'), text: await r.text() } });
    });
    it('unsatisfiable range', async () => {
      const r = await fetch(`${b.url}/files/programme.toml`, { headers: { Range: 'bytes=999999-' } });
      check('files-range-416', { status: r.status, type: '', body: { range: r.headers.get('content-range') } });
    });
    it('download', async () => {
      const r = await fetch(`${b.url}/files/programme.toml?download=1`);
      check('files-download', { status: r.status, type: '', body: r.headers.get('content-disposition') });
    });
    it('missing', async () => check('files-missing', await call('/files/nope.txt')));
    // fetch() resolves dot segments before sending, so these go out raw.
    it('escape refused', async () => check('files-escape', await rawGet('/files/../../etc/passwd', { Host: '127.0.0.1' })));
    it('encoded escape refused', async () => check('files-escape-encoded', await rawGet('/files/%2e%2e/%2e%2e/etc/passwd', { Host: '127.0.0.1' })));
  });

  describe('guards', () => {
    it('foreign host refused', async () => check('guard-host', await rawGet('/api/prefs', { Host: 'evil.example' })));
    it('foreign origin refused', async () => check('guard-origin', await rawGet('/api/prefs', { Host: '127.0.0.1', Origin: 'https://evil.example' })));
    it('unknown api route', async () => check('guard-404', await call('/api/nope')));
    it('bad command', async () => check('job-bad-command', await post('/api/jobs', { command: 'rm', targets: ['.'] })));
    it('bad argument', async () => check('job-bad-arg', await post('/api/jobs', { command: 'validate', targets: ['.'], args: { shell: 'x' } })));
    it('bad argument value', async () => check('job-bad-value', await post('/api/jobs', { command: 'validate', targets: ['.'], args: { lang: 'fr' } })));
    it('bad flag type', async () => check('job-bad-flag', await post('/api/jobs', { command: 'validate', targets: ['.'], args: { force: 'yes' } })));
    it('bad target', async () => check('job-bad-target', await post('/api/jobs', { command: 'validate', targets: ['../x'] })));
    it('no targets', async () => check('job-no-targets', await post('/api/jobs', { command: 'validate', targets: [] })));
    it('unknown job', async () => check('job-unknown', await call('/api/jobs/9999')));
    it('cancel unknown job', async () => check('job-cancel-unknown', await post('/api/jobs/9999/cancel', {})));
    it('empty intake', async () => check('intake-empty', await post('/api/intake', { text: '  ' })));
    it('import from outside returned/', async () => check('translation-import-outside', await post('/api/translation/import', { source: 'KV7015' })));
    it('upload that is not a zip', async () => {
      // Python leaves the unread body on a kept-alive connection; closing it keeps later requests clean.
      check('translation-upload-bad', await call('/api/translation/upload?name=x.txt', { method: 'POST', body: 'hello', headers: { Connection: 'close' } }));
    });
  });

  describe('writes and jobs', () => {
    it('prefs are clamped and saved', async () => {
      check('prefs-put', await post('/api/prefs', { jobs: 99, parallel_jobs: 0, poll_seconds: 1, operator: 'Tester', unknown: 1 }, 'PUT'));
      check('prefs-after', await call('/api/prefs'));
    });
    it('validate job runs to completion', async () => {
      const created = await post('/api/jobs', { command: 'validate', targets: [TOPIC], args: { lang: 'en', force: true } });
      check('job-validate-created', { ...created, body: { ...(created.body as object), state: 'queued-or-running' } });
      check('job-validate-done', jobShape(await waitForJob((created.body as { id: number }).id)));
    });
    it('ack job records the operator', async () => {
      const created = await post('/api/jobs', { command: 'ack', targets: [TOPIC], args: { fingerprint: 'MD_DATE|en|s1|x', from: '/etc/passwd' } });
      check('job-ack-done', jobShape(await waitForJob((created.body as { id: number }).id)));
    });
    it('intake check-only job', async () => {
      const src = (await call(`/api/topic/${TOPIC}/source`)).body as { text: string };
      const created = await post('/api/intake', { text: src.text, dry_run: true });
      check('intake-done', jobShape(await waitForJob((created.body as { id: number }).id)));
    });
    it('save job refuses a stale sha', async () => {
      const src = (await call(`/api/topic/${TOPIC}/source`)).body as { text: string };
      const created = await post(`/api/topic/${TOPIC}/save`, { text: src.text + '\n', lang: 'en', expect_sha: '0'.repeat(64) });
      check('save-conflict', jobShape(await waitForJob((created.body as { id: number }).id)));
    });
    it('jobs list after', async () => {
      const r = await call('/api/jobs?limit=3');
      const jobs = (r.body as { jobs: Array<Record<string, unknown>> }).jobs.map((j) => ({ ...j, progress: j.progress ? 'present' : null }));
      check('jobs-after', { ...r, body: { jobs } });
    });
    it('status refresh', async () => check('status-refresh', await post('/api/status/refresh', {})));
  });

  describe('events', () => {
    it('stream opens with hello', async () => {
      const ctrl = new AbortController();
      const r = await fetch(`${b.url}/api/events`, { signal: ctrl.signal });
      const reader = r.body!.getReader();
      const { value } = await reader.read();
      ctrl.abort();
      const first = new TextDecoder().decode(value).split('\n\n')[0];
      check('events-hello', { status: r.status, type: (r.headers.get('content-type') || '').split(';')[0],
        body: first.replace(/"status_version": ?\d+/, '"status_version": N') });
    });
  });

  describe('app', () => {
    it('serves the page for client routes', async () => {
      const r = await fetch(`${b.url}/some/client/route`);
      expect(r.status, await r.clone().text()).toBe(200);
      expect(r.headers.get('content-type')).toMatch(/^text\/html/);
    });
  });
});
