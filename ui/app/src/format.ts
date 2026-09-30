// Labels, help text and formatting shared by every page.

export const STAGE_LABEL: Record<string, string> = {
  planned: 'Planned', drafted: 'Drafted', validated: 'Validated', rendered: 'Rendered', recorded: 'Recorded',
  cued: 'Cued', packaged: 'Packaged', not_sent: 'Not sent', out_for_translation: 'Out for translation',
  returned: 'Returned', parity_checked: 'Parity checked',
};

export const NEXT_LABEL: Record<string, string> = {
  intake: 'write the script', validate: 'validate', render: 'render', await_recording: 'awaiting the edit',
  cues: 'cues', subtitles: 'subtitles', package: 'package', translation_export: 'send for translation',
  await_translation: 'awaiting translation',
};

// What each action does, shared by every page that offers it.
export const STEP_HELP: Record<string, string> = {
  validate: 'Check the script against the rules: structure, word count for the minutes, dates, forbidden words, '
    + 'phrases that point at the screen, title length, images. Quick. Run it after any edit.',
  render: 'Turn the slides into 1920×1080 images and a PDF deck in the house theme, and check the text fits '
    + 'and stays clear of the subtitle area. Needs validate to have passed.',
  script: 'Make the recording script: the narration alone, as a large-print PDF, plus copies for a teleprompter.',
  bumpers: 'Make the intro (the title over the background video) and the outro (the logo).',
  qti: 'Export unit quizzes as QTI 2.1 packages (.zip) to import into the LMS. A quiz with a question that has '
    + 'no correct answer, or fewer than two options, is not exported.',
  cues: 'Match the script to the editor\'s subtitles to find when each slide starts (the cue sheet), and list '
    + 'where the recording differs from the script. Needs the edited video and its subtitles. English only: '
    + 'Mandarin reuses the English timings.',
  subtitles: 'Prepare the subtitles for delivery, applying any approved fixes to misheard words (timings are never '
    + 'changed). For Mandarin, it checks the translator kept every timing exactly.',
  compose: 'Build a draft video (slides with the presenter inset and subtitles burned in) to check that the '
    + 'slides change at the right moments. For checking only; it is not delivered.',
  package: 'Assemble the delivery folder: the video, slide images, subtitles, cue sheet and script, with '
    + 'checksums. Needs cues and subtitles to be done.',
  qa: 'Run every check on the topic, plus cross-checks: video length, files older than the script, and '
    + 'misheard words not yet reviewed.',
  coursemap: 'Print course-map.md as a PDF, to share with collaborators who do not have the working copy.',
};

export const LANG_NAME = { en: 'English', zh: 'Mandarin' } as const;

export const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

export function fmtTime(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '—';
  const s = Math.max(0, sec);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}

export function fmtTC(sec: number): string {
  const ms = Math.round(sec * 1000);
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)}.${p(ms % 1000, 3)}`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—';
  const d = (now - new Date(iso).getTime()) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

/** KV7015-U01-T01 → KV7015/U01/T01 */
export const topicPath = (id: string) => id.split('-').join('/');

export const LEVEL_ORDER: Record<string, number> = { error: 0, warn: 1, info: 2 };
