// What every pane on the topic page reads: the topic's show and status, and a cache-buster
// per file so media reloads only when that file changes.
import type { InjectionKey, Ref } from 'vue';
import type { ShowResult, TopicStatus } from '@beacon/shared';

export interface TopicContext {
  id: string;
  rel: string;
  show: Ref<ShowResult | null>;
  status: Ref<TopicStatus | null>;
  /** The file's mtime, for ?v= on its URL. */
  stamp: (path: string) => string;
  reload: () => Promise<void>;
}

export const TOPIC: InjectionKey<TopicContext> = Symbol('topic');
