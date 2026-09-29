// In-process publish/subscribe feeding the browser's server-sent events stream.
import type { ServerEvent } from '@beacon/shared';

export type Subscriber = (event: ServerEvent) => boolean | void;

export class Bus {
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
