// The worker process: dequeues jobs from Postgres and runs bcn for each one. Runs
// alongside (not inside) the API process — see docker-compose.yml's "worker" service,
// which uses the same container image as "api" but this file as its entrypoint instead.
//
// Requires DATABASE_URL and REDIS_URL; BEACON_ROOT and BCN follow the same conventions
// as the API (see cli.ts).
import { resolve } from 'node:path';
import { Bcn, locateBcn } from './bcn.js';
import { checkRoot } from './app.js';
import { DB } from './db.js';
import { RedisBus } from './redis-bus.js';
import { Scheduler } from './scheduler.js';

async function main(): Promise<number> {
  const root = process.env.BEACON_ROOT;
  if (!root) { process.stderr.write('beacon-worker: BEACON_ROOT is required\n'); return 2; }
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) { process.stderr.write('beacon-worker: DATABASE_URL is required\n'); return 2; }
  const redisUrl = process.env.REDIS_URL;
  if (!redisUrl) { process.stderr.write('beacon-worker: REDIS_URL is required\n'); return 2; }

  const resolvedRoot = resolve(root);
  for (const w of checkRoot(resolvedRoot)) process.stderr.write(`warning: ${w}\n`);

  const db = new DB(databaseUrl);
  await db.init();
  const bus = new RedisBus(redisUrl);
  const bcn = new Bcn(locateBcn(process.env.BCN), resolvedRoot);
  const scheduler = new Scheduler(db, bus, bcn, resolvedRoot, () => db.prefs());

  process.stderr.write(`NUCS worker ${scheduler.workerId} for ${resolvedRoot}\n`);
  scheduler.start();

  const stop = () => {
    void scheduler.stop()
      .then(() => bus.close())
      .then(() => db.close())
      .finally(() => process.exit(0));
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  return 0;
}

main().then((code) => { if (code) process.exit(code); });
