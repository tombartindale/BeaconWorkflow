// A confirmation before anything runs, with room to say exactly what it will do.
import { Dialog } from 'quasar';
import ConfirmDialog, { type ConfirmLine } from '@/components/ConfirmDialog.vue';

export type { ConfirmLine };

export interface ConfirmOptions {
  title: string;
  lines?: Array<string | ConfirmLine>;
  ok?: string;
  /** Ask for a short piece of text as well; the promise then resolves with it. */
  prompt?: { placeholder: string };
  danger?: boolean;
}

/** Resolves true (or the typed text when `prompt` is set) if confirmed, false if not. */
export function confirm(opts: ConfirmOptions): Promise<false | true | string> {
  return new Promise((resolve) => {
    Dialog.create({ component: ConfirmDialog, componentProps: opts })
      .onOk((value: true | string) => resolve(value))
      .onCancel(() => resolve(false))
      .onDismiss(() => resolve(false));
  });
}
