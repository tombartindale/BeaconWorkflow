// Publish/subscribe feeding the browser's server-sent events stream.
//
// Bus is in-process only (fine for a single API replica). RedisBus fans the same events
// out over a Redis channel first, so every API replica's subscribers see every event
// regardless of which replica (or the worker) published it — needed once there is more
// than one API process, or a separate worker process publishing job progress.
import type { ServerEvent } from '@beacon/shared';

export type Subscriber = (event: ServerEvent) => boolean | void;

export interface EventBus {
  subscribe(fn: Subscriber): () => void;
  publish(event: ServerEvent): void;
}

export class Bus implements EventBus {
  private subs = new Set<Subscriber>();

  /** A subscriber returns false when it cannot keep up; it is then dropped and resyncs on reconnect. */
  subscribe(fn: Subscriber): () => void {
    this.subs.add(fn);
    return () => { this.subs.delete(fn); };
  }

  publish(event: ServerEvent): void {
    for (const fn of [...this.subs]) {
      try {
        if (fn(event) === false) this.subs.delete(fn);
      } catch {
        this.subs.delete(fn);
      }
    }
  }
}
