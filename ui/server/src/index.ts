// The Beacon UI backend as a library. createServer() never starts anything on import, so
// the CLI (cli.ts) and, later, an Electron main process can both start it the same way.
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { FastifyInstance } from 'fastify';
import { App } from './app.js';
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
  const app = new App({ root: opts.root, bcn: opts.bcn, dataDir: opts.dataDir });
  const http = await buildServer({ app, staticDir: opts.staticDir ?? DEFAULT_STATIC_DIR });
  const host = opts.host ?? '127.0.0.1';
  await http.listen({ host, port: opts.port });
  app.start();
  const address = http.server.address();
  const port = address && typeof address === 'object' ? address.port : opts.port;
  return {
    app, http, url: `http://${host}:${port}`,
    async close() {
      app.stop();
      await http.close();
    },
  };
}
