// A Bus backed by Redis pub/sub, so events published by any process (another API
// replica, or the worker running jobs) reach every subscriber in this process too.
//
// Redis requires a dedicated connection for subscribing (it cannot issue other commands
// once in subscribe mode), so publishing and subscribing use two separate clients.
import { Redis } from 'ioredis';
import type { ServerEvent } from '@beacon/shared';
import type { EventBus, Subscriber } from './bus.js';

const CHANNEL = 'beacon:events';

export class RedisBus implements EventBus {
  private subs = new Set<Subscriber>();
  private pub: Redis;
  private sub: Redis;
  private ready: Promise<void>;

  constructor(redisUrl: string) {
    this.pub = new Redis(redisUrl);
    this.sub = new Redis(redisUrl);
    this.ready = this.sub.subscribe(CHANNEL).then(() => undefined);
    this.sub.on('message', (_channel, message) => {
      let event: ServerEvent;
      try { event = JSON.parse(message); } catch { return; }
      for (const fn of [...this.subs]) {
        try {
          if (fn(event) === false) this.subs.delete(fn);
        } catch {
          this.subs.delete(fn);
        }
      }
    });
  }

  subscribe(fn: Subscriber): () => void {
    this.subs.add(fn);
    return () => { this.subs.delete(fn); };
  }

  publish(event: ServerEvent): void {
    // Fire-and-forget: SSE delivery is already best-effort (see Bus's own subscriber
    // backpressure handling), so a dropped publish is no worse than a dropped event.
    void this.pub.publish(CHANNEL, JSON.stringify(event));
  }

  async close(): Promise<void> {
    await this.ready;
    await Promise.all([this.pub.quit(), this.sub.quit()]);
  }
}
