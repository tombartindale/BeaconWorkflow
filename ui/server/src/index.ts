// The Beacon UI backend as a library. createServer() never starts anything on import, so
// the CLI (cli.ts) and, later, an Electron main process can both start it the same way.
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { FastifyInstance } from 'fastify';
import { App } from './app.js';
import { RedisBus } from './redis-bus.js';
import { buildServer } from './server.js';

export { App, checkRoot, defaultDataDir } from './app.js';
export { locateBcn } from './bcn.js';
export { StartupError } from './errors.js';

/** Where the built app lives when running from this repository. */
export const DEFAULT_STATIC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app', 'dist', 'spa');

export interface CreateServerOptions {
  root: string;
  bcn: string[];
  dataDir: string;
  port: number;
  host?: string;          // localhost only; see the README before changing it
  staticDir?: string;
}

export interface BeaconServer {
  app: App;
  http: FastifyInstance;
  url: string;
  close(): Promise<void>;
}

export async function createServer(opts: CreateServerOptions): Promise<BeaconServer> {
  // A Redis-backed bus is used when REDIS_URL is set, so job progress published by a
  // separate worker process reaches this API's SSE clients. Without it, jobs still get
  // enqueued and run (by a worker sharing the same Redis), but this API replica alone
  // would not see their progress live — only their final state, on its next poll.
  const redisBus = process.env.REDIS_URL ? new RedisBus(process.env.REDIS_URL) : undefined;
  const app = redisBus
    ? await App.create({ root: opts.root, bcn: opts.bcn, dataDir: opts.dataDir }, redisBus)
    : await App.create({ root: opts.root, bcn: opts.bcn, dataDir: opts.dataDir });
  const http = await buildServer({ app, staticDir: opts.staticDir ?? DEFAULT_STATIC_DIR });
  const host = opts.host ?? '127.0.0.1';
  await http.listen({ host, port: opts.port });
  app.start();
  const address = http.server.address();
  const port = address && typeof address === 'object' ? address.port : opts.port;
  return {
    app, http, url: `http://${host}:${port}`,
    async close() {
      await app.stop();
      if (redisBus) await redisBus.close();
      await http.close();
    },
  };
}
