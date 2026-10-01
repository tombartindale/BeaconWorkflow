// beacon-ui --root /path/to/programme
//
// Serves the UI (the API half only — see worker.ts for the process that actually runs
// bcn). Binds to 127.0.0.1 by default; pass --host to bind elsewhere, e.g. behind a
// reverse proxy in a multi-user deployment. See the README before exposing it directly.
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createServer, defaultDataDir, locateBcn, StartupError } from './index.js';

const USAGE = `usage: beacon-ui --root PATH [--port 8420] [--host 127.0.0.1] [--bcn PATH] [--data-dir PATH] [--static-dir PATH]

  --root        programme root (holds programme.toml); or set BEACON_ROOT
  --port        port to bind (default 8420, or BEACON_PORT)
  --host        address to bind (default 127.0.0.1)
  --bcn         path to the bcn executable (default: $BCN, tooling/.venv/bin/bcn, then PATH)
  --data-dir    where the UI keeps its pastes
  --static-dir  the built app to serve (default ui/app/dist/spa)

  DATABASE_URL  Postgres connection string for job history and preferences (required)
  REDIS_URL     enables live job progress across multiple api/worker processes (optional)`;

export async function main(argv: string[]): Promise<number> {
  let values;
  try {
    ({ values } = parseArgs({ args: argv, options: {
      root: { type: 'string' }, port: { type: 'string' }, host: { type: 'string' }, bcn: { type: 'string' },
      'data-dir': { type: 'string' }, 'static-dir': { type: 'string' }, help: { type: 'boolean', short: 'h' },
      // Undocumented on purpose: only the test suite passes this, to exercise the API
      // without a browser session. Never set it in a real deployment.
      'test-disable-auth': { type: 'boolean' },
    } }));
  } catch (e) {
    process.stderr.write(`beacon-ui: ${(e as Error).message}\n${USAGE}\n`);
    return 2;
  }
  if (values.help) { process.stdout.write(`${USAGE}\n`); return 0; }
  const rootArg = values.root || process.env.BEACON_ROOT;
  if (!rootArg) { process.stderr.write(`beacon-ui: --root is required (or set BEACON_ROOT)\n${USAGE}\n`); return 2; }
  const root = resolve(rootArg);
  const port = Number(values.port || process.env.BEACON_PORT || 8420);
  try {
    const dataDir = values['data-dir'] ? resolve(values['data-dir']) : defaultDataDir(root);
    const server = await createServer({ root, port, host: values.host, dataDir, bcn: locateBcn(values.bcn), staticDir: values['static-dir'],
      testDisableAuth: values['test-disable-auth'] });
    process.stderr.write(`NUCS UI for ${server.app.root}\n  ${server.url}\n  data: ${dataDir}\n`);
    for (const w of server.app.warnings) process.stderr.write(`  warning: ${w}\n`);
    const stop = () => { void server.close().finally(() => process.exit(0)); };
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
    return 0;
  } catch (e) {
    if (e instanceof StartupError) {
      process.stderr.write(`beacon-ui: ${(e as Error).message}\n`);
      return 2;
    }
    throw e;
  }
}

// Run directly (tsx src/cli.ts, or node dist/cli.js) rather than imported by bin/beacon-ui.
if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  main(process.argv.slice(2)).then((code) => { if (code) process.exit(code); });
}
