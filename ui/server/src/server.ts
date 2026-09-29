// HTTP server: JSON API, server-sent events, the app, and files from the root.
//
// Single user, bound to localhost, no authentication. Requests with a foreign Host or
// Origin header are refused so a web page elsewhere cannot drive it.
import { createReadStream, createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import type { IncomingMessage } from 'node:http';
import { basename, extname, join, relative, resolve, sep } from 'node:path';
import { pipeline } from 'node:stream/promises';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import type {
  BcnDiagnosticsEnvelope, BcnEditEnvelope, BcnReviewEnvelope, BcnStatusEnvelope, BcnSyncEnvelope, BootResponse, JobArgs,
  ShowEnvelope, TranslationItem,
} from '@beacon/shared';
import { jobLabel, type App } from './app.js';
import { BadRequest, BcnError, Forbidden } from './errors.js';
import { mimeType, withCharset } from './mime.js';
import { now, pyJson, sha256, stamp } from './util.js';

const BODY_MAX = 5 * 1024 * 1024;
const UPLOAD_MAX = 500 * 1024 * 1024;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
const TOPIC = '^[A-Z]{2}\\d{4}-U\\d{2}-T\\d{2}$';
const PING_MS = 15_000;
const SSE_BACKLOG = 4 * 1024 * 1024;  // a tab that falls this far behind is dropped; it resyncs on reconnect

type Query = Record<string, string | undefined>;
type Req = FastifyRequest<{ Params: Record<string, string>; Querystring: Query }>;

function hostname(value: string): string {
  if (value.startsWith('[')) return value.slice(0, value.indexOf(']') + 1);
  return value.split(':')[0];
}

function allowed(req: FastifyRequest): boolean {
  if (!LOCAL_HOSTS.has(hostname(req.headers.host || ''))) return false;
  const origin = req.headers.origin;
  if (origin) {
    try {
      if (!LOCAL_HOSTS.has(new URL(origin).hostname)) return false;
    } catch { return false; }
  }
  return true;
}

/** Every body arrives as a stream; routes read it as JSON, or stream it (uploads). */
async function readJson(req: FastifyRequest): Promise<Record<string, unknown>> {
  const n = Number(req.headers['content-length'] || 0);
  if (n > BODY_MAX) throw new BadRequest('Request too large.');
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req.body as IncomingMessage ?? []) {
    size += chunk.length;
    if (size > BODY_MAX) throw new BadRequest('Request too large.');
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString('utf8');
  let data: unknown;
  try { data = JSON.parse(text || '{}'); } catch (e) { throw new BadRequest((e as Error).message); }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new BadRequest('Expected a JSON object.');
  return data as Record<string, unknown>;
}

function toInt(value: unknown, name: string): number {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
  if (!Number.isFinite(n)) throw new BadRequest(`'${name}' must be a whole number.`);
  return Math.trunc(n);
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export interface ServerOptions { app: App; staticDir?: string }

export async function buildServer({ app, staticDir }: ServerOptions): Promise<FastifyInstance> {
  const f = Fastify({ logger: false, exposeHeadRoutes: true, bodyLimit: UPLOAD_MAX });
  f.removeAllContentTypeParsers();
  f.addContentTypeParser('*', (_req, payload, done) => done(null, payload));

  f.addHook('onRequest', async (req, reply) => {
    if (!allowed(req)) return reply.code(403).send({ error: 'Requests are accepted from localhost only.' });
    reply.header('Cache-Control', 'no-store');
    reply.header('X-Content-Type-Options', 'nosniff');
  });

  f.setErrorHandler((err: Error & { statusCode?: number }, _req, reply) => {
    if (err instanceof BadRequest) return reply.code(400).send({ error: err.message });
    if (err instanceof Forbidden) return reply.code(403).send({ error: err.message });
    if (err instanceof BcnError) return reply.code(502).send({ error: err.message });
    if (err.statusCode && err.statusCode < 500) return reply.code(err.statusCode).send({ error: err.message });
    return reply.code(500).send({ error: err.message || 'Internal error.' });
  });

  f.setNotFoundHandler((_req, reply) => reply.code(404).send({ error: 'Not found.' }));

  // -- API ------------------------------------------------------------------------------------
  f.get('/api/boot', async (): Promise<BootResponse> => ({
    root: app.root, prefs: app.db.prefs(), operator: app.operator(), warnings: app.warnings,
    doctor: app.doctor, codes: app.codes, status_version: app.status.version,
  }));

  f.get('/api/status', async (_req, reply) => {
    const env = await app.status.get();
    if (!env) return reply.code(503).send({ error: app.status.error || 'Status is not available yet.' });
    return { ...env, version: app.status.version, jobs_running: app.jobs.running() };
  });

  f.post('/api/status/refresh', async () => {
    app.clearQueryCache();
    await app.status.refresh('requested');
    return { version: app.status.version };
  });

  f.get(`/api/topic/:id(${TOPIC})`, async (req: Req) => {
    const id = req.params.id;
    const rel = app.targetRel(id);
    const path = app.targetPath(rel);
    if (req.query.verify) return app.bcn.query<BcnStatusEnvelope>('status', [path, '--verify']);
    const show = await app.cachedQuery<ShowEnvelope>(`show|${rel}`, 30_000, 'show', [path, '--asset-prefix', '/files/']);
    const env = await app.status.get();
    const row = env?.results?.find((r) => r.topic === id) ?? null;
    return { show, status: row };
  });

  // Script editing: bcn edit does the writing and checking; this only moves text.
  const editTextFile = (text: string): string => {
    const d = join(app.dataDir, 'edits');
    mkdirSync(d, { recursive: true });
    const old = readdirSync(d).filter((n) => n.endsWith('.md')).sort();
    for (const name of old.slice(0, Math.max(0, old.length - 50))) {
      try { unlinkSync(join(d, name)); } catch { /* already gone */ }
    }
    const file = join(d, `${stamp()}.md`);
    writeFileSync(file, text, 'utf8');
    return file;
  };
  const langOf = (v: unknown) => (v === 'zh' ? 'zh' : 'en');

  f.get(`/api/topic/:id(${TOPIC})/source`, async (req: Req) => {
    const rel = app.targetRel(req.params.id);
    const lang = req.query.lang ?? 'en';
    if (lang !== 'en' && lang !== 'zh') throw new BadRequest('lang must be en or zh');
    const p = join(app.root, rel, lang === 'en' ? 'topic.md' : 'topic.zh.md');
    const relPath = relative(app.root, p);
    if (!existsSync(p) || !statSync(p).isFile()) return { exists: false, text: '', sha256: null, path: relPath };
    const data = readFileSync(p);
    return { exists: true, text: data.toString('utf8').replace(/^﻿/, ''), sha256: sha256(data), path: relPath };
  });

  // Validate unsaved text (bcn edit --dry-run). Read-only, so it runs directly rather than as a job.
  f.post(`/api/topic/:id(${TOPIC})/check`, async (req: Req) => {
    const rel = app.targetRel(req.params.id);
    const data = await readJson(req);
    const file = editTextFile(String(data.text ?? ''));
    try {
      return await app.bcn.query<BcnEditEnvelope>('edit', [app.targetPath(rel), '--from', file, '--lang', langOf(data.lang), '--dry-run'], 60_000);
    } finally {
      try { unlinkSync(file); } catch { /* fine */ }
    }
  });

  f.post(`/api/topic/:id(${TOPIC})/save`, async (req: Req, reply) => {
    const rel = app.targetRel(req.params.id);
    const data = await readJson(req);
    const lang = langOf(data.lang);
    const args: JobArgs = { from: editTextFile(String(data.text ?? '')), lang };
    if (data.expect_sha && !data.overwrite) args.expect_sha = String(data.expect_sha);
    const job = app.jobs.submit('edit', [rel], args, `edit ${lang === 'zh' ? 'topic.zh.md' : 'topic.md'} · ${rel}`, app.operator());
    return reply.code(202).send(job);
  });

  f.get('/api/diagnostics', async (req: Req) => {
    const rel = app.targetRel(req.query.path ?? '.');
    return app.cachedQuery<BcnDiagnosticsEnvelope>(`diagnostics|${rel}`, 10_000, 'diagnostics', [app.targetPath(rel)]);
  });

  // Both directions as dry runs. They only stat files, so no cloud download is triggered.
  f.get('/api/sync', async () => {
    const [pull, push] = await Promise.all([
      app.cachedQuery<BcnSyncEnvelope>('sync|pull', 20_000, 'sync', [app.root, '--pull', '--dry-run']),
      app.cachedQuery<BcnSyncEnvelope>('sync|push', 20_000, 'sync', [app.root, '--push', '--dry-run']),
    ]);
    return { pull, push };
  });

  f.get('/api/review', async (req: Req) => {
    const rel = app.targetRel(req.query.path ?? '.');
    return app.cachedQuery<BcnReviewEnvelope>(`review|${rel}`, 10_000, 'review', [app.targetPath(rel)]);
  });

  // -- jobs -----------------------------------------------------------------------------------
  f.get('/api/jobs', async (req: Req) => ({ jobs: app.jobs.list(toInt(req.query.limit ?? '100', 'limit')) }));

  f.get('/api/jobs/:id(^\\d+$)', async (req: Req, reply) => {
    const job = app.jobs.get(Number(req.params.id));
    return job ?? reply.code(404).send({ error: 'No such job.' });
  });

  f.post('/api/jobs', async (req, reply) => {
    const data = await readJson(req);
    const command = String(data.command ?? '');
    const args: JobArgs = { ...((data.args && typeof data.args === 'object' ? data.args : {}) as JobArgs) };
    for (const k of ['from', 'import', 'by']) delete args[k];  // server-supplied only
    const targets = (Array.isArray(data.targets) ? data.targets : []).map((t) => app.targetRel(String(t)));
    if (command === 'review' || command === 'ack' || (command === 'cues' && (args.set || args.unset))) {
      args.by = app.operator() || 'ui';
    }
    return reply.code(202).send(app.jobs.submit(command, targets, args, jobLabel(command, targets, args), app.operator()));
  });

  f.post('/api/jobs/:id(^\\d+$)/cancel', async (req: Req, reply) => {
    const job = app.jobs.cancel(Number(req.params.id));
    return job ?? reply.code(404).send({ error: 'No such job, or it has already finished.' });
  });

  f.post('/api/intake', async (req, reply) => {
    const data = await readJson(req);
    const text = String(data.text ?? '');
    if (!text.trim()) throw new BadRequest('Paste some text first.');
    const d = join(app.dataDir, 'intake');
    mkdirSync(d, { recursive: true });
    const file = join(d, `${stamp()}.md`);
    writeFileSync(file, text, 'utf8');
    const target = app.targetRel(String(data.path || '.'));
    const args: JobArgs = { from: file, dry_run: Boolean(data.dry_run) };
    const job = app.jobs.submit('intake', [target], args, `intake${args.dry_run ? ' (check only)' : ''} · paste`, app.operator());
    return reply.code(202).send(job);
  });

  // -- translation ----------------------------------------------------------------------------
  f.get('/api/translation', async () => {
    const base = join(app.root, 'translation');
    const listing = (sub: string): TranslationItem[] => {
      const d = join(base, sub);
      if (!existsSync(d) || !statSync(d).isDirectory()) return [];
      return readdirSync(d).filter((n) => !n.startsWith('.')).sort().reverse().map((name) => {
        const p = join(d, name);
        const st = statSync(p);
        return { name, path: relative(app.root, p), kind: st.isFile() ? 'zip' : 'folder', bytes: st.isFile() ? st.size : null,
          mtime: now(st.mtime) };
      });
    };
    return { exports: listing('exports'), returned: listing('returned') };
  });

  f.post('/api/translation/upload', async (req: Req, reply) => {
    const name = (req.query.name ?? 'returned.zip').replace(/[^A-Za-z0-9._-]/g, '_');
    if (!name.toLowerCase().endsWith('.zip')) throw new BadRequest('Upload a .zip file.');
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    const d = join(app.root, 'translation', 'returned');
    mkdirSync(d, { recursive: true });
    let dest = join(d, name);
    if (existsSync(dest)) dest = join(d, `${basename(name, extname(name))}-${Math.floor(Date.now() / 1000)}.zip`);
    const tmp = join(d, `.${basename(dest)}.partial`);
    await pipeline(req.body as IncomingMessage, createWriteStream(tmp));
    renameSync(tmp, dest);
    return reply.code(201).send({ path: relative(app.root, dest) });
  });

  f.post('/api/translation/import', async (req, reply) => {
    const data = await readJson(req);
    const src = app.safePath(String(data.source ?? ''));
    const returned = app.safePath('translation/returned');
    if (!src.startsWith(returned + sep)) throw new Forbidden();
    const target = app.targetRel(String(data.path || '.'));
    const job = app.jobs.submit('translation', [target], { import: src }, `translation import · ${basename(src)}`, app.operator());
    return reply.code(202).send(job);
  });

  // -- prefs ----------------------------------------------------------------------------------
  f.get('/api/prefs', async () => app.db.prefs());

  f.put('/api/prefs', async (req) => {
    const data = await readJson(req);
    if ('jobs' in data) data.jobs = clamp(toInt(data.jobs, 'jobs'), 1, 16);
    if ('parallel_jobs' in data) data.parallel_jobs = clamp(toInt(data.parallel_jobs, 'parallel_jobs'), 1, 8);
    if ('poll_seconds' in data) data.poll_seconds = clamp(toInt(data.poll_seconds, 'poll_seconds'), 5, 600);
    return app.db.setPrefs(data);
  });

  // -- live events ----------------------------------------------------------------------------
  f.get('/api/events', (req, reply) => {
    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
    res.write(`event: hello\ndata: ${pyJson({ status_version: app.status.version })}\n\n`);
    const off = app.bus.subscribe((ev) => {
      if (res.writableLength > SSE_BACKLOG) { res.destroy(); return false; }
      res.write(`data: ${JSON.stringify(ev)}\n\n`);
    });
    const ping = setInterval(() => res.write(': ping\n\n'), PING_MS);
    req.raw.on('close', () => { clearInterval(ping); off(); });
  });

  // -- files from the root --------------------------------------------------------------------
  f.get('/files/*', async (req: Req, reply) => {
    const rawPath = (req.raw.url || '').split('?')[0];
    const p = app.safePath(rawPath.slice('/files/'.length));
    if (!existsSync(p) || !statSync(p).isFile()) return reply.code(404).send({ error: 'No such file.' });
    const q = req.query;
    const ext = extname(p).toLowerCase();
    if (ext === '.srt' && q.format === 'vtt') {
      const text = (await readFile(p)).toString('utf8').replace(/^﻿/, '').replace(/\r\n/g, '\n');
      const vtt = 'WEBVTT\n\n' + text.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
      return reply.type('text/vtt; charset=utf-8').send(vtt);
    }
    if (q.view && ['.md', '.txt', '.json', '.toml', '.csv', '.srt', '.vtt'].includes(ext)) {
      // For the in-app document viewer: always text, never a download.
      return reply.type('text/plain; charset=utf-8').send(await readFile(p));
    }
    const size = statSync(p).size;
    reply.header('Accept-Ranges', 'bytes');
    reply.header('Content-Type', mimeType(p));
    if (q.download) reply.header('Content-Disposition', `attachment; filename="${basename(p)}"`);
    const m = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || ''));
    if (m && (m[1] || m[2])) {
      let start: number, end: number;
      if (m[1]) {
        start = Number(m[1]);
        end = m[2] ? Number(m[2]) : size - 1;
      } else {
        start = Math.max(0, size - Number(m[2]));
        end = size - 1;
      }
      end = Math.min(end, size - 1);
      if (start > end || start >= size) {
        return reply.code(416).header('Content-Range', `bytes */${size}`).type('text/plain').send('');
      }
      reply.code(206).header('Content-Range', `bytes ${start}-${end}/${size}`).header('Content-Length', String(end - start + 1));
      return reply.send(createReadStream(p, { start, end }));
    }
    reply.header('Content-Length', String(size));
    return reply.send(createReadStream(p));
  });

  // -- the app --------------------------------------------------------------------------------
  const root = staticDir ? resolve(staticDir) : null;
  const serveApp = async (req: FastifyRequest, reply: FastifyReply) => {
    const path = (req.raw.url || '/').split('?')[0];
    if (path.startsWith('/api/')) return reply.code(404).send({ error: 'Not found.' });
    if (!root || !existsSync(join(root, 'index.html'))) {
      return reply.type('text/html; charset=utf-8').send(
        '<!doctype html><title>Beacon</title><p>The Beacon app has not been built yet. Run <code>scripts/setup.sh</code>, or <code>npm run build</code> in <code>ui/</code>.</p>');
    }
    let rel = path.replace(/^\/+/, '');
    try { rel = decodeURIComponent(rel); } catch { /* use as is */ }
    let p = resolve(root, rel || 'index.html');
    if (!p.startsWith(root + sep) || !existsSync(p) || !statSync(p).isFile()) p = join(root, 'index.html');  // client-side routes
    // Built assets have hashed names and never change; the page itself must always be fresh.
    if (p.includes(`${sep}assets${sep}`)) reply.header('Cache-Control', 'public, max-age=31536000, immutable');
    return reply.type(withCharset(mimeType(p))).send(await readFile(p));
  };
  f.get('/', serveApp);
  f.get('/*', serveApp);

  return f;
}

